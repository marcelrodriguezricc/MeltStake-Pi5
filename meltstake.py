# pyright: reportMissingImports=false
import logging
import logging.handlers
import re
import json
import time
import numpy as np
from math import sin, cos, asin, atan2, sqrt, pi
from threading import Thread, Lock
import traceback
import socket
import serial
from datetime import datetime, timezone
import inspect
import board
from brping import Ping1D      
import ms5837
from digitalio import DigitalInOut, Direction, Pull  # GPIO module
import smbus2
from pca9685 import PCA9685
from ads1115 import ADS1115
from icm20602 import ICM20602
from mmc5983 import MMC5983


# assign log file
logging.basicConfig(level=logging.DEBUG, filename="/home/pi/data/meltstake.log", filemode="a+",
                    format="%(asctime)-15s %(levelname)-8s %(message)s")

# get device ID_number from static IP
try:
    f = open("/etc/dhcpcd.conf", "r")
    sstrn = 'static ip_address=10.0.1.1'
    for line in f:
        if line.startswith(sstrn):
            DEV_NO = re.split(sstrn+'|/',  line)[1]
except Exception:
    DEV_NO = '00'

# Mutex init
mutex = Lock()

"""
Create a simple PCA9685 class instance.
"""
try:
    # create PCA class
    pca = PCA9685()
    # pca.write(0x00, [0xb6]) #reset PCA IC (may need more... check datasheet "7.6 Software Reset")
    # arm PCA
    pca.output_enable()
    # Set the PWM frequency.
    pca.set_pwm_frequency(200)
    # Create funtion for interfacing with Blue Robotics components
    def WRITE_DUTY_CYCLE(channel:int, value:float) -> bool:
        """ Write speed value to the PCA channel associated with this motor ID """
        time.sleep(0.05)
        if 0 <= channel <= 15:
            if -1.0 <= value <= 1.0:
                pca.pwm[channel]=1500+400*value
                exit_condition = True
            else:
                logging.error("Invalid motor speed: %s. Value must be between -1 and +1.", str(value))
                exit_condition =  False
        else:
            logging.error("Invalid PCA channel: %s. PCA9685 has 16 channels (0 -> 15).", str(value))
            exit_condition =  False
        
        return exit_condition
    
except Exception as error:
    LOG_STRING = "failed to initialize PCA9685 driver:, " + str(error)
    logging.error(LOG_STRING)


# Initialize ADS1115 on meltstake PCB.
try:
    battery_ads = ADS1115(13)
    battery_ads.VOLTAGE = [0,0,0,0]
    def monitor_battADS():
        while True:
            for i in [0,1,3]:
                try:
                    battery_ads.VOLTAGE[i] = battery_ads.read(i)
                    time.sleep(0.1)
                except:
                    pass
    Thread(daemon=True, target=monitor_battADS).start()
except Exception as error:
    LOG_STRING = "Error encountered while initializing ADS1115 driver on i2c bus 13:, " + str(error)
    logging.error(LOG_STRING)
    
# initialize navigator ADS
try:
    navigator_ads = ADS1115(1)
    navigator_ads.VOLTAGE = [0,0,0,0]
    def monitor_navADS():
        while True:
            for i in [0]:
                try:
                    navigator_ads.VOLTAGE[i] = navigator_ads.read(i)
                    time.sleep(0.1)
                except:
                    pass
    Thread(daemon=True, target=monitor_navADS).start()
except Exception as error:
    LOG_STRING = "Error encountered while initializing ADS1115 driver on i2c bus 1:, " + str(error)
    logging.error(LOG_STRING)


def bound(value, lwr=0, upr=1):
    """ useful function to limit a value to range [lwr, upr] """
    return max(min(upr, value), lwr)

def timer(func):   
    """ Wrapper for functions. Will return execution time in seconds """ 
    def inner(*args, **kwargs):
        t0 = time.time()
        result = func(*args, **kwargs)
        t1 = time.time()
        execution_time = t1-t0
        return execution_time
    return inner


class LED:
    
    __pin_no = 25
    
    def __init__(self, pin_no=__pin_no):
        self.pin_no = pin_no
        self.__led = DigitalInOut(eval("board.D"+str(self.pin_no)))
        self.__led.direction = Direction.OUTPUT
    
    @property
    def pin_no(self):
        """Gets pin number of LED"""
        return self.__pin_no
    
    @pin_no.setter
    def pin_no(self, value=int) -> bool:
        if 1 <= value <= 26:
            self.__pin_no = value
            return True
        else:
            logging.warning("GPIO pin number %s undefined on Raspberry Pi 4.", str(value))
            return False
    
    def off(self):
        self.__led.value = True
    
    def on(self):
        self.__led.value = False
    
    def blink(self, frequency:float=2.):
        """Continuously blink the LED at some user-defined frequency.

        Args:
            frequency (float, optional): Frequency of blinking in Hz. Defaults to 2.
        """
        def blink_loop():
            while True:
                self.on()
                time.sleep(1/frequency)
                self.off()
                time.sleep(1/frequency)
        t_blink = Thread(target=blink_loop, daemon=True)
        t_blink.start()


class Battery:
    """
    Class for monitoring battery voltage
    """
    __voltage = 0.0
    __voltage_limit = 13.5
    __under_voltage = False

    def __init__(self, voltage_limit=__voltage_limit):
        self.voltage_limit = voltage_limit
        
        t_monitor_voltage = Thread(target=self.monitor_voltage, daemon=True)
        t_monitor_voltage.start()
        
    @property
    def voltage(self):
        """ Gets voltage """
        return self.__voltage

    @voltage.setter
    def voltage(self, value:float) -> bool:
        """ Sets latest measured voltage """
        if 0 <= value <= 16.9:
            self.__voltage = value
            return True
        else:
            LOG_STRING = "Battery voltage reading outside of expected limits: " + str(value)
            logging.warning(LOG_STRING)
            return False
            
    @property
    def voltage_limit(self):
        """ Gets voltage limit """
        return self.__voltage_limit

    @voltage_limit.setter
    def voltage_limit(self, value:float) -> bool:
        """ Sets the voltage limit """
        if 12.8 <= value <= 16.8:
            self.__voltage = value
            return True
        else:
            LOG_STRING = "Minimum battery voltage limit set to a value outside of expected range:, " + str(value)
            logging.warning(LOG_STRING)
            return False
            
    @property
    def under_voltage(self):
        """ Gets battery charge state """
        return self.__under_voltage

    @under_voltage.setter
    def under_voltage(self, value:bool) -> bool:
        """ Sets battery charge state """
        self.__under_voltage = value
        return True
    
    # -----------------------------
    
    def monitor_voltage(self):
        """ Thread to monitor voltage and update battery charge state """
        BATT_VOLT_DIV_RATIO = (3.3 + 10) / 3.3  # R1 = 10kOhm; R2 = 3.3kOhm
        while True:
            self.voltage = battery_ads.VOLTAGE[3] * BATT_VOLT_DIV_RATIO
            if self.voltage <= self.voltage_limit:
                self.under_voltage = True
            else:
                self.under_voltage = False
            time.sleep(0.25)
    

class LeakDetection:
    
    __state = False

    def __init__(self):
        t_monitor_leak = Thread(target=self.monitor_leak, daemon=True)
        t_monitor_leak.start()
    
    @property
    def state(self):
        """Gets leak state"""
        return self.__state

    @state.setter
    def state(self, value:bool) -> bool:
        """Sets the leak state"""
        self.__state = value
        
    def monitor_leak(self):
        """ Monitors and updates leak state. 
        This value is set in LeakState.txt by LeakDetection.service
        """
        while True:
            with open('/home/pi/MeltStake/ServiceScripts/LeakState.txt', "r") as f: 
                self.state = eval(f.readline())
            time.sleep(0.25)
            

class Drill:
    """
    Provides a model for Blue Robotics T200 gearbox-reduced ice screw
    """
    __ID_number = 0
    __speed = 0
    __current_speed = 1 # set high so motors will initialize to zero speed
    __current_limit = 14
    __current_draw = 0
    __overdrawn = False
    __pulses = 0

    def __init__(self, ID_number, current_limit=__current_limit):
        # initialize drill object
        self.ID_number = ID_number
        self.current_limit = current_limit
        self.auto_release_OVRD = False
        self.auto_release_kill = False
        
        # start threads --------------------
        t_speed_manager = Thread(target=self.update_speed, daemon=True)
        t_speed_manager.start()

        t_current_monitor = Thread(target=self.monitor_current, daemon=True)
        t_current_monitor.start()

        t_count_pulses = Thread(target=self.count_pulses, daemon=True)
        t_count_pulses.start()

    @property
    def ID_number(self):
        """ Gets the motor ID_number """
        return self.__ID_number
    
    @ID_number.setter
    def ID_number(self, value:int) -> bool:
        """ sets the motor ID_number """
        if 0 <= value <= 1:
            self.__ID_number = value
            return True
        else:
            logging.error("Invalid motor ID_number: %s. Value must be 0 or 1.", str(value))
            return False
    
    @property
    def speed(self):
        """ Gets the motor speed """
        return self.__speed

    @speed.setter
    def speed(self, value:float) -> bool:
        """ Sets the motor target speed """
        if -1 < value < 1:
            self.__speed = value
            return True
        else:
            logging.error("Invalid motor speed request: %s. Value must be between -1.0 and 1.0", str(value))
            return False
        
    @property
    def current_speed(self):
        """ Gets the current motor speed """
        return self.__current_speed
    
    @current_speed.setter
    def current_speed(self, value:float) -> bool:
        """Sets the current motor speed"""
        if -1 < value < 1:
            self.__current_speed = value
            return True
        else:
            logging.error("Invalid motor speed: %s. Value must be between -1.0 and 1.0", str(value))
            return False
    
    @property
    def current_limit(self):
        """ Gets the current limit (Amps) """
        return self.__current_limit
    
    @current_limit.setter
    def current_limit(self, value:float) -> bool:
        """ Sets the current limit (Amps) """
        if value > 0.:
            self.__current_limit = value
            return True
        else:
            logging.error("Invalid curent limit: %s", str(value))
            return False
    
    @property
    def current_draw(self):
        """Gets current draw"""
        return self.__current_draw
    
    @current_draw.setter
    def current_draw(self, value:float) -> bool:
        self.__current_draw = abs(value)
        return True
    
    @property
    def overdrawn(self):
        """ Gets the current limiting state """
        return self.__overdrawn
    
    @overdrawn.setter
    def overdrawn(self, value:bool) -> bool:
        """ sets the current limiting state """
        self.__overdrawn = value
        return True
    
    @ property
    def pulses(self):
        """ Gets number of pulses """
        return self.__pulses
    
    @pulses.setter
    def pulses(self, value:int) -> bool:
        """ Sets number of pulses """
        if value >= 0:
            self.__pulses = value
            return True
        else:
            logging.error("Invalid pulse count: %s", str(value))
            return False
    
    
    # ----------------------------------
        
    def update_speed(self):
        """Monitors requested motor speed and updates PWM signal sent to ESCs accordingly
        """
        while True:
            if abs(self.current_speed - self.speed) > 0.01:
                mutex.acquire()
                print(str(self.ID_number)+" Mutex aquired")
                try:
                    logging.info('writing speed '+str(self.speed)+' to motor #'+str(self.ID_number)+'. Current speed is '+str(self.current_speed))
                    WRITE_DUTY_CYCLE(self.ID_number, self.speed)
                    self.current_speed = self.speed
                except Exception as e:
                    logging.error("PWM write failed for motor "+str(self.ID_number))
                    logging.error("ERROR : " + str(e))
                mutex.release()
                print(str(self.ID_number)+" Mutex released")
            time.sleep(0.05) 
    
    
    def monitor_true_speed(self):
        """ Not currently in use. Needs more de-bugging. """
        min_reads = 4
        while True:
            pwm_last_measured = 0 
            mutex.acquire()
            for i in range(min_reads):
                print(str(self.ID_number)+'::    mutey acquy')
                try:
                    pwm_measured = pca.pwm[self.ID_number]
                    print(str(self.ID_number)+'::    '+str(i)+": "+str(pwm_measured)+" | "+str(pwm_last_measured))
                    if i > 0 and pwm_measured != pwm_last_measured:
                        print(str(self.ID_number)+'::    '+'incorrect read **************************************************')
                        break
                    elif i is min_reads-1:
                        self.current_speed = (float(pwm_measured) - 1500) / 400
                        print(str(self.ID_number)+'::    '+str((float(pwm_measured) - 1500) / 400))
                except Exception as e:
                    print(str(self.ID_number)+'::    '+'bad read **************************************************')
                    logging.error("PWM read failed for motor "+str(self.ID_number))
                    logging.error(str(e))
                    break
                pwm_last_measured = pwm_measured
                print(str(self.ID_number)+'::    '+'mutey reley')
                time.sleep(0.01)
            mutex.release()
            time.sleep(0.05)
    
    def monitor_current(self):
        """ Monitor current draw of motor, set speed to zero if it exceeds limit """
        CURR_DRAW_DIV_RATIO = 1  # no volt divider
        while True:
            self.current_draw = 10 * (2.5 - battery_ads.VOLTAGE[self.ID_number] * CURR_DRAW_DIV_RATIO)
            if self.current_draw > self.current_limit:
                self.overdrawn = True
                self.speed = 0.0 # set speed to zero
            else:
                self.overdrawn = False
            time.sleep(0.1)
    
    def count_pulses(self):
        """
            Count pulses of reed sensor on output shaft (1 pulse/rotation)
            Algorithm based on debounce.c written by Kenneth A. Kuhn
        """
        DEBOUNCE_TIME = 0.003
        SAMPLE_FREQUENCY = 1000
        MAXIMUM = DEBOUNCE_TIME * SAMPLE_FREQUENCY
        integrator = 0
        output = 0
        prior_output = 0
        if self.ID_number == 0:
            pin = DigitalInOut(board.D4)
        else:
            pin = DigitalInOut(board.D8)
        pin.direction = Direction.INPUT
        pin.pull = Pull.DOWN
        
        while True:
            time.sleep(1/SAMPLE_FREQUENCY)
            input = pin.value
            if input == 0:
                if integrator > 0:
                    integrator = integrator - 1
            elif integrator < MAXIMUM:
                integrator = integrator + 1
            if integrator == 0:
                output = 0
            elif integrator >= MAXIMUM:
                output = 1
                integrator = MAXIMUM
            if output == 0 and prior_output == 1:
                self.pulses = self.pulses + 1
            prior_output = output

    def auto_release_timer(self, depth, release_flag):
        """
        Start a timer. At 5 minuntes trigger auto-release.
        This is a preventative measure for the failure mode of ROV comm loss while drilled into ice.
        note: only triggers when measuring below-surface depths
        """
        t0 = time.time()
        self.auto_release_kill = False
        release_flag[0] = False
        while True:
            t = time.time() - t0
            if t > 5*60:
                release_flag[0] = True
                logging.info("Auto release initiated")
                break
            if self.auto_release_OVRD == True:
                break
            if self.auto_release_kill == True:
                break
            time.sleep(0.01)
            
        return


class SubLight:
    """" 
    Provides a model of Blue Robotics sub light
    """
    __channel = 15
    __brightness = 0

    def __init__(self, channel=__channel, brightness=__brightness):
        self.channel = channel
        self.brightness = brightness

    @property
    def channel(self):
        """ Gets sublight channel """
        return self.__channel

    @channel.setter
    def channel(self, value:int) -> bool:
        """ Sets Sublight channel """
        if 0 <= value <= 15:
            self.__channel = value
            return True
        else:
            logging.error("Invalid PCA channel: %s. PCA9685 has 16 channels (0 -> 15).", str(value))
            return False
        
    @property
    def brightness(self):
        """ Gets Sublight brightness """
        return self.__brightness
    
    @brightness.setter
    def brightness(self, value:float) -> bool:
        """ Sets Sublight brightness """
        if 0. <= value <= 1.:
            self.__brightness = value
            mapped_inp = 2*self.brightness - 1 # map to [-1,1]
            if not mutex.locked():
                WRITE_DUTY_CYCLE(self.channel, mapped_inp)
            return True
        else:
            logging.error("Invalid Sublight brightness value: %s. Must be between 0 and 1", str(value))
            return False
  
  
class SonarCommChannel:
  """ Implement the transport send and receive to the Sonar881 controller.
  """

  def __init__(self):
    pass

  def __enter__(self):
    self.ser = serial.Serial('/dev/serial0', baudrate=115200, bytesize=8, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE)
    # We wake up once in a while so the program can be interrupted.
    self.ser.timeout = 0.5
    return self
  
  def __exit__(self, *args):
    self.ser.close()

  def receiveStatus(self) -> str:
    """ Wait forever for a switch command, and return it as a byte array.
    """
    got_line = False
    read_data = bytearray(0)
    while not got_line:
      partial_data = self.ser.read_until(b'\n')
      read_data.extend(partial_data)
      if len(read_data) > 0 and read_data[-1] == 0x0a:
        got_line = True

    return eval(read_data.decode('utf-8').rstrip())

  def receiveResponse(self) -> str:
    read_data = self.ser.read_until(b'\n')
    return read_data

  def sendCommand(self, command:object):
    """ Serializes and sends a command object to the Sonar881 controller.
        Typically, this will be a dictionary, but limited arrays are also allowed.
    """
    commandString = json.dumps(command) + '\n'
    sent_count = self.ser.write(bytes(commandString, "utf-8"))
    self.ser.flush()
    if sent_count != len(commandString):
      logging.info('Sent ' + str(sent_count) + ' bytes, but should have sent ' + len(commandString))

  
class LimitSwitch:
    """
    Provides actuated distance control for our in-house temperature sensor rake.
    """
    
    """
    A1302 Hall effect sensor constants
    """
    def __init__(self):
        # Tare background field to zero
        self.threshold = 0.02
        self.perc = 0
        self.flag = False
        self.override = False
        self.BASE_OUT = 0
        time_avg = 1
        freq = 100
        self.VOLT_DIV_RATIO = (470 + 470) / 470  # R1 = 470kOhm; R2 = 470kOhm
        time.sleep(1)
        for i in range(int(time_avg*freq)):
            self.BASE_OUT = self.BASE_OUT + navigator_ads.VOLTAGE[0]*self.VOLT_DIV_RATIO
            time.sleep(1/freq)
        self.BASE_OUT = self.BASE_OUT/int(time_avg*freq)
        
        Thread(target=self.monitor_limit_switch, daemon=True).start()


    def tare(self):
        override_state_holder =  self.override
        self.override = True
        self.BASE_OUT_temp = 0
        time_avg = 1
        freq = 100
        time.sleep(1)
        for i in range(int(time_avg*freq)):
            self.BASE_OUT_temp = self.BASE_OUT_temp + navigator_ads.VOLTAGE[0]*self.VOLT_DIV_RATIO
            time.sleep(1/freq)
        self.BASE_OUT = self.BASE_OUT_temp/int(time_avg*freq)
        self.override = override_state_holder

    def monitor_limit_switch(self):
        
        while True:
            if not self.override:
                self.perc = abs((2*navigator_ads.VOLTAGE[0] - self.BASE_OUT)/2.5)
            else:
                self.perc = 0
                
            if self.perc > self.threshold:
                self.flag = True
            else:
                self.flag = False
            
            time.sleep(0.05)

    
class Beacon:
    """
    Provides a model of the Succorfish Delphis beacon modem
    """

    __recieved_msg = ''
    __transmit_msg = ''

    def __init__(self):
        t_listen = Thread(target=self.listen, daemon=True)
        t_listen.start()
        
    @property
    def recieved_msg(self):
        """ Gets latest recieved message """
        return self.__recieved_msg

    @recieved_msg.setter
    def recieved_msg(self, value:str) -> bool:
        """ Sets latest recieved message """
        self.__recieved_msg = value
        return True
    
    @property
    def transmit_msg(self):
        """ Gets latest transmit message """
        return self.__transmit_msg

    @transmit_msg.setter
    def transmit_msg(self, value:str) -> bool:
        """ Sets latest transmit message """
        try:
            HOST = '127.0.0.1'
            PORT = 4000
            s = socket.socket()
            s.connect((HOST, PORT))
            # fullmsg = 'MS-' + str(DEV_NO) + ' RE: ' + value
            fullmsg = '106 RE: ' + value
            s.send(fullmsg.encode())
            s.close()
            logging.info(fullmsg)
            self.__transmit_msg = value
            return True
        except Exception:
            logging.info("ERROR transmitting message: " + str(value))
            logging.info(traceback.format_exc())
            return False

    # -----------------------------
    
    def listen(self):
        """ Thread to listen for incoming messages """
        PORT = 3000
        HOST = '127.0.0.1'
        MAX_LENGTH = 4096
        serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        serversocket.bind((HOST, PORT))
        serversocket.listen(5)
        while True:
            (clientsocket, address) = serversocket.accept()
            msg = clientsocket.recv(MAX_LENGTH)
            if msg == '':  # client terminated connection
                clientsocket.close()
            self.recieved_msg = msg.decode()
    

class Sensors:    
    """
    Contains sensors and data collection
    """
    
    __record = []
    __all_sensors = {
            "Battery" : "IV",
            "Rotations" : "ROT",
            # "Ping" : "PING",
            "Orientation" : "IMU",
            "Pressure" : "PT",
            "LimitSwitch" : "LS"
        }
    __sample_rate = 10
    __DATA_INIT_DELAY = 2
    
    def __init__(self, battery, motors, limitswitch, sample_rate=__sample_rate, record=list(__all_sensors.keys())):
        # self.__battery = battery
        # self.__motors = motors
        
        self.sample_rate = sample_rate
        self.record = record
        
        for sensor in self.record:
            """ Initialize sensor objects """
            
            if sensor == "Battery":
                self.IV = []
                self._battery = self.Battery(battery, motors)
                sensor_obj = self._battery
            elif sensor == "Rotations":
                self.ROT = []
                self._rotations = self.Rotations(motors)
                sensor_obj = self._rotations
            elif sensor == "Ping":
                self.PING = []
                self._ping = self.Ping()
                sensor_obj = self._ping
            elif sensor == "Orientation":
                self.IMU = []
                self._orientation = self.Orientation()
                sensor_obj = self._orientation
            elif sensor == "Pressure":
                self.PT = []
                self._pressure = self.Pressure()
                sensor_obj = self._pressure
            elif sensor == "LimitSwitch":
                self.LS = []
                self._limitswitch = self.LimitSwitch(limitswitch)
                sensor_obj = self._limitswitch
                    
            if not sensor_obj.init():
                logging.warning(sensor + " sensing failed to initialize. Not recording data")
            else:
                Thread(daemon=True, target=self.record_data_cont, args=(sensor,)).start()
            
            
    @property
    def sample_rate(self):
        """ Gets the data sampling rate in Hz """
        return self.__sample_rate

    @sample_rate.setter
    def sample_rate(self, value:float) -> bool:
        """Sets data sampling rate (Hz)"""
        if value < 0:
            logging.error("Negative sampling rate specified. ")
            return False
        elif value > 20:
            logging.warning("Processor unable to consistently sample above 20 Hz. Sample rate set to 20 Hz.")
            self.__sample_rate = 20
            return True
        else:
            self.__sample_rate = value
            return True
    
    @property
    def record(self):
        """ Gets list of sensors that will be recording data """
        return self.__record
    
    @record.setter
    def record(self, value:list) -> bool:
        """Sets list of sensors that will be recording data """
        record = []
        for string in value:
            if string in list(self.__all_sensors.keys()):
                record.append(string)
            else:
                logging.error("Invalid sensor requested")
                return False
        self.__record = record
        return True

    def WriteToFile(self, data_list = None, data_bin = False):
        # use outer function name as data_bin if not specified
        if data_bin == False:
            cframe = inspect.currentframe()
            data_bin = inspect.getframeinfo(cframe.f_back).function
        
        # Filenaming convention: /home/pi/data/<data_bin>.dat
        dt = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        current_date = dt[0:10]
        current_time = dt[0:23]
        filename = "/home/pi/data/" + data_bin + ".dat"

        # Write new line to file. Format: current_time    data_1    ...    data_n
        if data_list != None:
            with open(filename, "a+") as f:  
                # write new data line
                f.write(current_time + "\t")
                str_data = [ f"{data_point:.3f}" if isinstance(data_point, float) else str(data_point) for data_point in data_list]
                for data_point in str_data:
                    f.write(data_point)
                    f.write("\t")
                f.write("\n")
                
    @timer
    def record_data(self, data_type:str):
        data_var = self.__all_sensors[data_type]
        exec("self." + data_var + " = self._" + data_type.lower() + ".read()" )
        data = eval("self._" + data_type.lower()+".read()")
        self.WriteToFile( data, data_type )

    def record_data_cont(self, data_type:str):
        time.sleep(self.__DATA_INIT_DELAY)
        while True:
            exec_time = self.record_data(data_type)
            sleep_time = max(0, 1/self.sample_rate - exec_time)
            time.sleep(sleep_time)

    class Battery:
        def __init__(self, battery, motors):
            self.battery = battery
            self.motors = motors
        
        def init(self) -> bool:
            if not self.read():
                logging.info("Battery reading failed!")
                return False
            return True
        
        def read(self) -> list:
            try:
                IV = []
                IV.append(self.battery.voltage)
                for motor in self.motors:
                    IV.append(motor.current_draw)
            except Exception:
                IV = []
            return IV

    class Pressure: 
        def init(self) -> bool:
            self.sensor = ms5837.MS5837_30BA(13)
            if not self.sensor.init():  # initialize sensor
                logging.info("Pressure sensor could not be initialized")
                return False
            if not self.sensor.read():  # Check we can read from sensor
                logging.info("Pressure sensor read failed!")
                return False
            return True
        
        def read(self) -> list:       
            self.sensor.read()
            P = self.sensor.pressure(ms5837.UNITS_atm)
            T = self.sensor.temperature(ms5837.UNITS_Centigrade)
            PT = [P,T]
            return PT
        
    class Rotations:
        def __init__(self, motors):
            self.motors = motors
            
        def init(self) -> bool:
            if not self.read():
                logging.info("Rotation tracking sensor read failed!")
                return False
            return True
        
        def read(self) -> list:  # to be ran as thread
            try:
                # for motor in self.motors:
                #     print(motor.pulses)
                ROT = [motor.pulses for motor in self.motors]
                return ROT
            except Exception:
                return False
    
    class Ping():
        def init(self) -> bool:
            self.sensor = Ping1D()
            self.sensor.connect_serial("/dev/ttyAMA0", 115200)
            
            if not self.sensor.initialize():
                logging.warning("Ping sensor could not be initialized")
                return False
            if not self.read():
                logging.warning("Ping sensor could not be read")
                return False
            return True

        def read(self):
            try:
                ping_data = self.sensor.get_distance()
                PING = [ping_data["distance"], ping_data["confidence"]]
                return PING
            except Exception:
                return False
    
    class Orientation():
        class ImuMag:

            def __init__(self):
                self.icm = ICM20602()
                self.mmc = MMC5983(i2cbus=None)

            def get_imu(self):
                data = self.icm.read_all()
                acc = [data.a.x, data.a.y, data.a.z]
                # gyr = [data.g.x, data.g.y, data.g.z]
                return acc

            def get_mag(self):
                # read the data
                data = self.mmc.read_data()
                mag = [data.x, data.y, data.z]
                # mag_raw = [data.x_raw, data.y_raw, data.z_raw]
                return mag

            def cal_mag(self):
                self.mmc.calibrate()
                # cal = [self.mmc.caldata[0], self.mmc.caldata[1], self.mmc.caldata[2]]

            def process_imu(self, accRaw):
                """ Processes raw IMU data to derive pitch and roll.
                Pitch is positive when the bow lifts up.
                Roll is positive when the port side lifts up.
                """
                # desired XYZ directions (standard)
                # X - point toward bow
                # Y - point to port
                # Z - point up

                # uncomment the relevant lines depending on the IMU orientation
                accRaw[0] = -accRaw[0]  # imu x axis points to the stern
                accRaw[1] = -accRaw[1]  # imu y axis points to the starboard
                accRaw[2] = -accRaw[2]  # imu z axis points down
                try:
                    # normalize the raw accelerometer data
                    norm_factor = sqrt(accRaw[0] * accRaw[0] + accRaw[1] * accRaw[1] + accRaw[2] * accRaw[2])
                    accXnorm = accRaw[0] / norm_factor
                    accYnorm = accRaw[1] / norm_factor
                    pitch = asin(accXnorm)
                    roll = -asin(accYnorm/cos(pitch))
                    # convert from radians to degrees
                    pitch_deg = pitch * (180/pi)
                    roll_deg = roll * (180/pi)
                except Exception as e:
                    pitch_deg = 0
                    roll_deg = 0
                return pitch_deg, roll_deg

            def process_mag(self, magRaw, pitch_deg, roll_deg):
                """ Processes raw magnetometer data to derive heading.
                Compensates for pitch and roll.
                Heading is measured clockwise in degrees from magnetic north.
                Pitch and roll are expected to be in degrees.
                *** This method is not outputting correct heading values. ***
                """

                # desired XYZ directions (standard)
                # X - point toward bow
                # Y - point to port
                # Z - point up

                # uncomment the relevant lines depending on the magnetometer orientation
                # magRaw[0] = -magRaw[0]  # mag x axis points to the stern
                magRaw[1] = -magRaw[1]  # mag y axis points to the starboard
                # magRaw[2] = -magRaw[2]  # mag z axis points down

                # convert from degrees to radians
                pitch = pitch_deg * (pi/180)
                roll = roll_deg * (pi/180)

                # compensate for the pitch and roll
                magXcomp = magRaw[0]*cos(pitch) + magRaw[2]*sin(pitch)
                magYcomp = magRaw[0]*sin(roll)*sin(pitch) + magRaw[1]*cos(roll) - magRaw[2]*sin(roll)*cos(pitch)
                # magXcomp = magRaw[0]*cos(pitch) - magRaw[2]*sin(pitch)
                # magYcomp = magRaw[0]*sin(roll)*sin(pitch) + magRaw[1]*cos(roll) + magRaw[2]*sin(roll)*cos(pitch)

                # compensated heading in radians
                heading = atan2(magYcomp, magXcomp)

                # convert from radians to degrees
                heading_deg = heading * (180/pi)

                # convert heading from math angles (degrees CCW from x) to azimuth (degrees CW from north)
                # heading_deg = 90 - heading_deg

                # keep heading in the range 0 - 360
                if heading_deg < 0:
                    heading_deg += 360

                return heading_deg

            def main(self):

                self.cal_mag()
                acc = self.get_imu()
                mag = self.get_mag()

                pitch, roll = self.process_imu(acc)
                heading = self.process_mag(mag, pitch, roll)

                return pitch, roll, heading
            
            
        def init(self) -> bool:
            try:
                self.sensor = self.ImuMag()  # initialize IMU
                self.read()
                return True
            except Exception:
                logging.info("IMU sensor could not be initialized")
                return False

        def read(self):
            IMU = self.sensor.main()
            return IMU


    class LimitSwitch:
        def __init__(self, limitswitch):
            self.limitswitch = limitswitch
        
        def init(self) -> bool:
            if not self.read():
                logging.info("Limit switch reading failed!")
                return False
            return True

        def read(self) -> list:
            try:
                LS = []
                LS.append(self.limitswitch.perc)
                LS.append(self.limitswitch.BASE_OUT)
                LS.append(self.limitswitch.threshold)
            except Exception:
                LS = []
            return LS

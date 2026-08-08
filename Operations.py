# pyright: reportMissingImports=false
import logging
import os
import time
import numpy as np
from threading import Thread, Event
import traceback
from datetime import datetime

import meltstake as ms

# assign log file
logging.basicConfig(level=logging.DEBUG, filename="/home/pi/data/meltstake.log", filemode="a+",
                    format="%(asctime)-15s %(levelname)-8s %(message)s")

motors = [ms.Drill(0), ms.Drill(1)]
for motor in motors: # start threads
    motor.start()
battery = ms.Battery()
battery.start()
limitswitch = ms.LimitSwitch()
limitswitch.start()
data = ms.Sensors(battery, motors, limitswitch)
light = ms.SubLight()
leaksensor = ms.LeakDetection()
leaksensor.start()
heartbeat = ms.LED(25)
heartbeat.blink()
SOS = ms.LED(11)
SOS.off() 

disarm = False
SOS_flag = False
stopauto = True
num_motors = len(motors)
max_speed = 0.6
auto_release_event = Event()
auto_release_thread = Thread()

def DRILL(target_turns):  
    """ Power each motor for the specified # of turns.

    Args:
        target_turns (int): Target number of revolutions. Negative for CCW, Positive for CW.
    """
    global disarm
    global auto_release_event
    global auto_release_thread
       
    #start new auto release timer thread
    AR_RESET()
    
    # clean up input: 
    target_turns = [int(str_in) for str_in in target_turns]  # convert string input to int
    target_turns.extend([0] * (num_motors - len(target_turns)))  # pad end with 0's if input was less than number of motors
    target_turns = target_turns[0:num_motors]  # remove extra elements if larger than the number of motors

    # initial number of turns
    turns = [0]*num_motors

    # determine direction(s) to move
    directions = [np.sign(goal-current) for goal,current in zip(target_turns,turns)]

    # intialize flag conditional
    target_reached = [False]*num_motors

    disarm = False
    # Loop to drive motors. Exit conditions:
    #   - target number of rotations reached
    #   - power drawn beyond current limit
    #   - limit switch is triggered
    while any([not done for done in target_reached]) and not disarm:

        # update starting number of turns
        offsets = [motor.pulses for motor in motors]  # initial # of pulses

        # adjust speed for each motor:
        for motor_no, (motor, dir, done, targ, curr) in enumerate(zip(motors, directions, target_reached, target_turns, turns)):
            if not done:
                if dir * (targ - curr) <= 0 or motor.overdrawn or (motor.speed >= 0 and dir > 0 and limitswitch.flag):
                    target_reached[motor_no] = True
                    motor.speed = 0
                else:
                    motor.speed = dir * max_speed

        time.sleep(0.05)  # give some time for system to move

        # determine number of signed rotations since last iteration
        change_in_turns = [np.sign(motor.current_speed) * (motor.pulses - offset) for motor, offset in zip(motors,offsets)]
        turns = [int(sum(x)) for x in zip(turns, change_in_turns)]  # update turn counter

    # Once we reach our target turns, set all speeds to zero and break
    OFF()
    return

def AUTO(deployment_intv_time):
    """ Operation for autonomous deployment.

    Args:
        deployment_intv_time (list): A list of integers defining
            1) Rotations per drill attempt
            2) time between drill attempts (minutes)
            3) total deployment time (minutes)
    """
    global stopauto
    global SOS_flag    
    
    # disable auto-release
    AR_OVRD('T')

    rotations_per_drill = float(deployment_intv_time[0])
    time_between_drills = float(deployment_intv_time[1])
    deployment_time = float(deployment_intv_time[2])

    OFF()

    # tare rotation tracker
    SETROT([0,0])

    init_time = time.time()
    last_drill_time = init_time
    
    stopauto = False
    while ((time.time()-init_time) < (deployment_time*60)) and not SOS_flag and not stopauto:

        time.sleep(0.1)

        # wait specified time between drill attempts
        if (time.time()-last_drill_time) > (time_between_drills*60): 
            # try drilling in. 
            # For both screws this will either do the full 20 rotations or over-current out
            Thread(daemon=True, target=DRILL, args=([rotations_per_drill]*2, )).start()
            last_drill_time = time.time()
            logging.info("AUTONOMOUS DRILLING")


    OFF()
    if not stopauto: # if we didnt manually stop AUTO, RELEASE from ice
        time.sleep(1)
        RELEASE()

    return

def RELEASE(arguments=None):  
    """ Release unit from ice (note: approx 36 rotations for length of ice screw).
    This function will first read the most recent pressure measurements.
    If depth is determined to be greater than 0.5 meters, initiate release.
    Monitors rotations, if the number is not increasing (ie screws are stuck) it will try
    drilling into the ice for 5 rotations, then back out.

    Args:
        arguments (_type_, optional): Not currently utilized.
    """
    global stopauto
    
    max_rotations = 500 # maximum number of rotations allowed before exiting RELEASE
    rotations_init = data.ROT
    
    logging.info("begining RELEASE operation")
    
    # disable auto-release
    AR_OVRD('T')

    stopauto = False
    underwater = False
    underwater_confirmed_count = 0
    surface_confirmed = False
    surface_confirmed_count = 0

    def read_pressure_sample(max_lines=100, freshness_window_seconds=5.0):
        """Return the latest pressure sample from the pressure log file if it is fresh."""
        pressure_file = "/home/pi/data/Pressure.dat"
        if not os.path.exists(pressure_file):
            return None

        try:
            with open(pressure_file, "r") as fh:
                lines = [line.strip() for line in fh if line.strip()]
        except Exception:
            return None

        now = time.time()
        for line in reversed(lines[-max_lines:]):
            try:
                parts = line.split('\t')
                if len(parts) < 2:
                    continue
                timestamp = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
                depth = float(parts[1])
                if np.isfinite(depth) and (now - timestamp.timestamp()) <= freshness_window_seconds:
                    return timestamp, depth
            except Exception:
                continue
        return None

    def monitor_depth():
        """Operation to monitor fresh pressure readings before and during release."""
        nonlocal underwater, underwater_confirmed_count, surface_confirmed, surface_confirmed_count

        last_timestamp = None
        while not stopauto and not surface_confirmed:
            try:
                sample = read_pressure_sample()
                if sample is not None:
                    timestamp, depth = sample
                    if last_timestamp is None or timestamp > last_timestamp:
                        last_timestamp = timestamp
                        if depth > 1.05:
                            underwater_confirmed_count += 1
                            if underwater_confirmed_count >= 3:
                                underwater = True
                            surface_confirmed_count = 0
                        else:
                            underwater_confirmed_count = 0
                            surface_confirmed_count += 1
                            if surface_confirmed_count >= 3:
                                surface_confirmed = True
                                break
                    else:
                        ms.logwait("Pressure timestamp has not advanced; waiting for fresh reading")
                else:
                    ms.logwait("No fresh pressure sample in log; waiting for fresh reading")
            except Exception:
                ms.logwait("Bad pressure reading; waiting for fresh reading")

            time.sleep(0.25)
        return

    Thread(daemon=True, target=monitor_depth).start()

    loops = 0
    in_attempts = -1
    wait_time = 1
    drill_out = [-1000, -1000] # some big number

    start_deadline = time.time() + 3.0
    while not stopauto and not underwater and not surface_confirmed and time.time() < start_deadline:
        time.sleep(0.1)

    if not underwater:
        if surface_confirmed:
            logging.info("Release not started: pressure indicates the unit is at the surface")
            OFF()
            AR_OVRD('F')
            return
        logging.info("Pressure state unclear; starting release")
    else:
        logging.info("Underwater confirmed; starting release")

    OFF()
    Thread(daemon=True, target=DRILL, args=(drill_out,)).start()
    motors[0].auto_release_OVRD = True
    release_CLA_time = time.time()
    while not stopauto and not surface_confirmed and max(np.array(data.ROT) - np.array(rotations_init)) < max_rotations:
             
        time.sleep(wait_time)

        try:
            # every 20 seconds check if # of rotations are increasing:
            if loops*wait_time > 20: 
                loops = 0
                # [Rread, delta_rot0, delta_rot1] = get_saved_data("Rotations", 2)
                try:
                    rotations_t0 = data.ROT
                    time.sleep(1)
                    delta_rotations = np.array(data.ROT) - np.array(rotations_t0)
                    Rread = True
                except Exception:
                    Rread = False
                    
                if (np.any(delta_rotations == 0)) or not Rread: #if either stake is stuck
                    # attempt to drill in 2 turns (this sometimes helps loosen the ice)
                    # alternate between left, right, and both drilling in
                    in_attempts = (in_attempts + 1) % 3

                    if in_attempts == 1:
                        drill_in = [2, 0]
                    elif in_attempts == 2:
                        drill_in = [0, 2]
                    else:
                        drill_in = [2, 2]

                    OFF() # stop drill out
                    time.sleep(0.5)
                    Thread(daemon=True, target=DRILL, args=(drill_in,)).start() # start drill in
                    motors[0].auto_release_OVRD = True
                    time.sleep(5)
                    OFF() # stop drill in
                    time.sleep(0.5)
                    Thread(daemon=True, target=DRILL, args=(drill_out,)).start() # resume drill out
                    motors[0].auto_release_OVRD = True
            
            try:
                if time.time() - release_CLA_time > 300:  # every 5 minutes RELEASE is running, increase current limit by 1A
                    active_current_limit = motors[0].current_limit
                    if active_current_limit < 30:
                        CLA(active_current_limit + 1)
                        logging.info("Increased current limit from " + str(active_current_limit) + "A to " + str(motors[0].current_limit) + "A for release operation")
                        release_CLA_time = time.time()  # reset timer to allow repeated increases
            except Exception:
                logging.info("Failed to increase current limit for release operation")
                logging.info(traceback.format_exc())
                pass
                
        except Exception:
            logging.info("RELEASE operation failed")
            logging.info(traceback.format_exc())
            logging.info("Continuing RELEASE operation...")
            pass
        
        loops = loops+1

    logging.info("exiting RELEASE operation")

    OFF()
    
    # re-enable auto-release
    AR_OVRD('F')
    
    return

def OFF(arguments=None):  
    """ Sets all motor speeds to zero

    Args:
    """
    global disarm
    
    disarm = True
    for motor in motors:
        motor.speed=0 
    return

def AR_OVRD(state):
    
    try:
        if state[0] == 'T' or state == '1':
            motors[0].auto_release_OVRD = True
        elif state[0] == 'F' or state == '0':
            motors[0].auto_release_OVRD = False
    except Exception:
        logging.info("Failed to set auto-release override state")
        logging.info(traceback.format_exc())
        pass
    
def AR_RESET(arguments=None):
    global auto_release_thread, auto_release_event
    if not motors[0].auto_release_OVRD:
        try:
            motors[0].auto_release_kill = True
            time.sleep(0.05)
            auto_release_event = Event()  # fresh event
            auto_release_thread_new = Thread(daemon=True, target=motors[0].auto_release_timer, args=(auto_release_event,))
            if not auto_release_thread.is_alive():
                auto_release_thread = auto_release_thread_new
                auto_release_thread.start()
        except Exception as e:
            logging.info("Auto-release reset failed.")
            logging.info(traceback.format_exc())
            pass

def LS_TARE(arguments=None): 
    
    limitswitch.tare()
    logging.info("limit switch tared.")

def LS_THRESH(value):
    
    try:
        value = value[0]
        limitswitch.threshold = float(value)
        logging.info("limit switch threshold value updated: "+ str(limitswitch.threshold))
    except Exception as e:
        logging.info("limit switch threshold update failed.")
        logging.info(traceback.format_exc())
    
        pass

def LS_OVRD(state): 
    """Override limit switch auto stop for drilling. 
    
    Args:
        state : T, 1  --> disable limit switching
              : F, 0  --> enable limit switching
    """
    try:
        if state[0] == 'T' or state[0] == '1':
            limitswitch.override = True
        elif state[0] == 'F' or state[0] == '0':
            limitswitch.override = False
    except Exception:
        pass
    
def SETROT(set_turns):  
    """ Manually overwrite rotation tracking number

    Args:
        set_turns (str): String of integers providing new value for rotations. "motor0_value motor1_value ..."
    """
    
    try:
        # clean up input:
        set_turns = [int(str_in) for str_in in set_turns]  # convert string input to int
        set_turns.extend([None] * (num_motors - len(set_turns)))  # pad end with None's if input was less than number of motors
        set_turns = set_turns[0:num_motors]  # remove extra elements if larger than the number of motors

        for motor, set_turn in zip(motors, set_turns):
            if set_turn != None:
                motor.pulses = set_turn
    except Exception as e:
        logging.info("motor rotation tracking adjustment failed")
        logging.info(traceback.format_exc())
    
    return

def SETSPD(new_spd):
    """Sets the default speed for drill operations.

    Args:
        arguments (str): String of a float providing new speed value. Must be between 0 and 1
    """
    global max_speed
    
    try:
        flt_spd = [float(str_spd) for str_spd in new_spd]
        flt_spd = flt_spd[0]
        if flt_spd is not None and 0. <= flt_spd <= 1.:
            max_speed = flt_spd
    except Exception as e:
        logging.info("motor speed adjustment failed")
        logging.info(traceback.format_exc())
    
    return
        
def DATA(beacon, arguments=None):
    """ Send most recent data measurement via beacon tx

    Args:
        data (ms.Sensors): object created by "ms.Sensors" class
        beacon (Beacon): object created by "Beacon" class
        arguments (string): Data type requested. Options:
            IV     ::  current, voltage
            ROT    ::  rotations
            PING   ::  ping sonar
            IMU    ::  pitch, tilt, roll (?)
            PT     ::  pressure, temperature from ms5837

    """
    # send most recent data measurement via beacon tx

    for data_req in arguments:
        logging.info("Attempting to transmit "+data_req+" data ... ")
        time.sleep(1)
        try:
            data_list = getattr(data, data_req)  # get most recent measurement
            str_dat = [ f"{data_point:.3f}" if isinstance(data_point, float) else str(data_point) \
                        for data_point in data_list]
            msg = data_req + " " + ' '.join(str_dat)
            beacon.transmit(msg)  # transmit requested data
        except Exception as e:
            logging.info("Data transmission failed")
            logging.info(traceback.format_exc())
            pass

    return

def CLA(new_current_limit):
    """ Manually overwrite current limit on motors

    Args:
        new_current_limit (str): String of a float providing new value for current limit (Amps).
    """
    try:
        new_current_limit = float(new_current_limit[0])
        
        for motor in motors:
            motor.current_limit = new_current_limit
    except Exception as e:
        logging.info("Current limit adjust failed")
        logging.info(traceback.format_exc())

    return

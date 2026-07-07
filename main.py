#!/home/pi/meltstake/venv/bin/python
# pyright: reportMissingImports=false
import os
import time
import traceback
import logging
import argparse
from threading import Thread

from meltstake import Beacon
import Operations

# parse arguements
argParser = argparse.ArgumentParser()
argParser.add_argument("-m", "--mode", help=" mode of operation. Options: debug, deploy", default='deploy')
args = argParser.parse_args()

# assign log file
logging.basicConfig(level=logging.DEBUG, filename="/home/pi/data/meltstake.log", filemode="a+",
                    format="%(asctime)-15s %(levelname)-8s %(message)s")

# Initialize beacon object
beacon = Beacon()
beacon.start()  # start beacon listening thread

t_operation = Thread()  # initialize main thread variable
known_commands = [attribute for attribute in dir(Operations) if \
                    callable(getattr(Operations, attribute)) and attribute.startswith('__') is False]

if args.mode == 'debug':
    Operations.max_speed = 0.2
    tutorial_msg= """
    COMMAND OPTIONS:
        - DRILL <ARG1> <ARG2> : 
            ARG1 = number of turns to drill motor 0
            ARG2 = number of turns to drill motor 1
        - LIGHT <ARG1> :
            ARG1 = brightness value 0 <--> 100
        - RELEASE : release melt stake from ice face
        - OFF : turn all motors off
        - SETSPD <ARG1> :
            ARG1 = set motor speed (0.0 < ARG1 < 1.0)
        - AUTO <ARG1> <ARG2> <ARG3> :
            ARG1 = Rotations per drill attempt
            ARG2 = time between drill attempts (minutes)
            ARG3 = total deployment time (minutes)
    """
    print(tutorial_msg)

# startup_time = 3
# time.sleep(startup_time)

# MAIN LOOP:
while not Operations.battery.under_voltage and not Operations.leaksensor.state:
    try:
        time.sleep(0.05)

        ### Receive/transmit beacon messages
        if args.mode == 'debug':
            beacon.received_msg = input("input: ")  # Terminal input for testing.

        if beacon.received_msg != '' or Operations.auto_release_event.is_set():
            
            if Operations.auto_release_event.is_set():
                msg = 'RELEASE'
                Operations.auto_release_event.clear()
            else:
                msg = beacon.received_msg
                
            msg = msg.upper()

            beacon.transmit(msg)# echo back the message
            
            msg_split = msg.split()
            command = msg_split[0]
            arguments = [msg_split[i] for i in range(1,len(msg_split))]
            

            if command == 'OFF':
                Operations.OFF()
                Operations.stopauto = True
            
            elif command == 'STOPAUTO':
                Operations.stopauto = True
                
            elif command == 'AR_OVRD':
                Operations.AR_OVRD(arguments)
                
            elif command == 'LS_OVRD':
                Operations.LS_OVRD(arguments)

            elif command == 'CLA':
                Operations.CLA(arguments)

            elif command == 'DATA':
                Operations.DATA(beacon, arguments)

            elif command == 'LIGHT':
                try:
                    flt_in = float(arguments[0])
                    Operations.light.brightness = flt_in/100
                except:
                    pass

            elif command in known_commands:  # any other commands will begin as a thread
                func = getattr(Operations, command)
                t_new = Thread(daemon=True, target=func, args=(arguments,))
                
                if not t_operation.is_alive():  # don't overwrite any currently running operations
                    t_operation = t_new
                    t_operation.start()
                else:
                    beacon.transmit("BUSY")
                    print("operation currently running... send 'OFF' to kill")
            
            beacon.received_msg = ''


    except Exception:
        logging.info("--- RUNTIME ERROR: ---")
        logging.info(traceback.format_exc())
        break

# set all motors & sublights to off before exiting code
Operations.SOS_flag = True # this flag tells AUTONOMOUS operation subroutine to exit
Operations.OFF()
if not Operations.stopauto:
    Operations.stopauto = True
Operations.light.brightness = 0.0

time.sleep(1) # give some time for any currently running operations to wrap up

# If we're in deployment mode begin a RELEASE thread
if args.mode != 'debug':
    Thread(daemon=True, target=Operations.RELEASE).start()

# transmit SOS call via beacon every 5 seconds
if Operations.leaksensor.state:
    SOS_msg = "LEAK DETECTED!"
elif Operations.battery.under_voltage:
    SOS_msg = "LOW BATTERY! : "+f"{Operations.battery.voltage:.1f}"+"V"
else:
    SOS_msg = "UNKNOWN ERROR"
Operations.SOS.blink(10)


while True:
    try:
        beacon.transmit("SOS")
        time.sleep(5)
        beacon.transmit(SOS_msg)
    except Exception:
        pass
    time.sleep(5)
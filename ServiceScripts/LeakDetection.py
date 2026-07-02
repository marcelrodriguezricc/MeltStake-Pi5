#!/home/pi/meltstake/venv/bin/python
# pyright: reportMissingImports=false
import board
from digitalio import DigitalInOut, Direction, Pull  # GPIO module
import time

# init Leak sensor GPIO
leak = DigitalInOut(board.D27)
leak.direction = Direction.INPUT
leak.pull = Pull.DOWN

with open('/home/pi/meltstake/ServiceScripts/LeakState.txt', "w") as f:  
    # write new data line
    f.write("False")

while leak.value == False:
    time.sleep(0.25)

with open('/home/pi/meltstake/ServiceScripts/LeakState.txt', "w") as f:  
    # write new data line
    f.write("True")

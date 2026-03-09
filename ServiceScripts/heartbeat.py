#!/home/pi/meltstake/venv/bin/python
# pyright: reportMissingImports=false
import board
from digitalio import DigitalInOut, Direction
import time

# Initialize LED 2, turn off, then deinitialize
led2 = DigitalInOut(board.D11)
led2.direction = Direction.OUTPUT
led2.value = True
led2.deinit()

# Initialize LED 3, turn off, then deinitialize
led3 = DigitalInOut(board.D25)
led3.direction = Direction.OUTPUT
led3.value = True
led3.deinit()

# Initialize LED 1, turn off, then blink indefinitely
led1 = DigitalInOut(board.D24)
led1.direction = Direction.OUTPUT
led1.value = True
while True:
    led1.value = False
    time.sleep(1)
    led1.value = True
    time.sleep(1)
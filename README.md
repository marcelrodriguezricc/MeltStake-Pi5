
Code for melt stake control, interfacing, and data collection.

Confirmed to work on Raspberry Pi OS Lite (64-bit)

# Installation
The simplest installation method is via git:
- `sudo apt update`
- `sudo apt install -y git`
- `cd ~/`
- `git clone https://github.com/marcelrodriguezricc/meltstake`
- `cd meltstake`
- `sudo bash setup.sh '00'  # replace '00' with the melt stake ID number`

The default IP address for all Raspberry pi on a shared network can be found by running:
- `arp -n -a | grep -i ' 28:\| B8:\| D8:\| DC:\| E4:'`

If installing by a remote connection, connection is likely to be lost when network settings are updated — the setup will still run.

# Standard Operating Procedure
For long-term deployment, hand-load melt stake onto Walrus (delivery ROV). SSH into Walrus (`pi@192.168.1.3`(?) psk:`companion`) and navigate to `/home/pi/nav`. Run `tail -f unicast.txt` to monitor for any messages received from the melt stake. Run 
`send_command.py <BEACON_ID> DRILL 25 25` 
when in range of the ice face (see interfacing section for more details). Once both ice screws are fully seated in the ice, run 
`send_command.py <BEACON_ID> OFF` 
`send_command.py <BEACON_ID> SETROT 0 0` 
On confirming counters have been reset (`send_command.py <BEACON_ID> DATA ROT`), detatch the ROV from the melt stake and swim away. 
`send_command.py <BEACON_ID> DRILL 1000 1000  # arbitrary big numbers`

Melt stake will send rotation and ping data automatically every 60 seconds. The data and frequency can be adjusted in `/home/pi/nav/config.yaml`

# Over current protection
Each of the motors are equipped with an ACS712 current sensor. If at any point this sensor detects a current draw at or exceeding 13 amps (user adjustable, `current_limit` in the ADC class), the motor will auto-stop.

# Arguments
There are two modes of operation of the melt stake, specified by the tag `-m`:
- `deploy` : Field deployment
- `debug` : motor speed is reduced to 20%, the beacon input is replaced with command line input, and the release protocol is not called on exiting the code.

# Interfacing
Commands to the melt stake are sent through acoustic beacons. To transmit a command run 
- `send_command.py <BEACON_ID> <COMMAND>`
Where `<BEACON_ID>` corresponds to the receiving beacon ID number on the melt stake:
- `202` for melt stake 02
- `209` for melt stake 03
and `<COMMAND>` is a command from the following list of options:
- `OFF` : power down all motors and kill any other currently running operations.
- `DRILL <ROTATIONS_1> <ROTATIONS_2> ... <ROTATIONS_n>` : Turn motor 1,2,...,n some integer number of rotations CW (pos) or CCW(neg).
- `RELEASE` : Turns all motors CCW for 50 rotations. Used to release melt stake from ice.
- `AUTONOMOUS <` : For autonomous field deployment. This command is to be called once the melt stake has been manually set into the ice. This operation will continuously attempt to drill into the ice a set number of rotations, sleeping for a set amount of time between attempts. 
- `LIGHT <BRIGHTNESS>` : adjust the light brightness level. `<BRIGHTNESS>` is a number between 0 and 100. Defaults to 50%.
- `SETROT <ROTATIONS_1> <ROTATIONS_2> ... <ROTATIONS_n>` : Manually overwrite the saved rotation tracking count for each motor.  To leave a motor's rotation counter un-altered, use input `-1` for that motor. 
This is a useful command for zeroing the rotation count following intial deployment. Note: this requires sending a preceding `OFF` command then re-starting a `DRILL` operation
- `DATA <DATA_TYPE_1> <DATA_TYPE_2> ... ` : Requests the most recent measurement from the specified data set(s). Options for `<DATA_TYPE_1>` are:
    - -   `IV`     ::  current, voltage
    - -   `ROT`    ::  rotations
    - -   `PING`   ::  ping sonar
    - -   `IMU`    ::  pitch, tilt, roll (?)
    - -   `PT`     ::  pressure, temperature from ms5837


All messages received by the ROV from the melt stake will be logged in `/home/pi/nav/unicast.txt`. Upon receiving a message (e.g., `<COMMAND>`) the melt stake will always send a confirmation reply in the format `RE: <COMMAND>`
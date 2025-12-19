#!/bin/bash

# ----- SETUP FUNCTIONS -----

# Print helper functions
log()    { echo -e "\033[1;34m[INFO]\033[0m $1"; }
ok()     { echo -e "\033[1;32m[ OK ]\033[0m $1"; }
warn()   { echo -e "\033[1;33m[WARN]\033[0m $1"; }
error()  { echo -e "\033[1;31m[FAIL]\033[0m $1"; }

# Avoid redunant WiFi connections when creating new ones
cleanup_con() {

  # Pass in local variable with connection name
  local NAME="$1"

  # List connections, filter for exact name, and test existancel; Delete redunant connections; report if deleted correctly, warn if delete fails. If no connection is found, skip
  if nmcli -t -f NAME con show | grep -Fx "$NAME" >/dev/null; then
    log "Found redundant NetworkManager connection: $NAME"
    nmcli -t -f NAME con show | grep -Fx "$NAME" | while read -r _; do
      nmcli con delete "$NAME" >/dev/null 2>&1 \
        && ok "Deleted connection: $NAME" \
        || warn "Failed to delete connection: $NAME"
    done
  else
    log "No existing connection found for: $NAME"
  fi
}

# Install/Update a Python repo and install it into the virtual environment
install_python_repo() {

  # Arguments
  local REPO_URL="$1"
  local REPO_NAME="$2"
  local LABEL="$3"
  local BRANCH="${4:-}" 
  local REPO_PATH="$PKG_DIR/$REPO_NAME"

  log "Installing ${LABEL} from ${REPO_URL}${BRANCH:+ (branch: $BRANCH)}..."


  # Clone or update
  if [ ! -d "$REPO_PATH/.git" ]; then
    log "Cloning ${REPO_NAME} into: $REPO_PATH"
    if [ -n "$BRANCH" ]; then
      git clone --single-branch --branch "$BRANCH" "$REPO_URL" "$REPO_PATH" \
        && ok "Cloned ${REPO_NAME} (branch: $BRANCH)" \
        || { error "Failed to clone ${REPO_NAME} (branch: $BRANCH)"; return 1; }
    else
      git clone "$REPO_URL" "$REPO_PATH" \
        && ok "Cloned ${REPO_NAME}" \
        || { error "Failed to clone ${REPO_NAME}"; return 1; }
    fi
  else
    log "Repo already exists: $REPO_PATH (updating)"
    (
      cd "$REPO_PATH" || exit 1
      git fetch --all --tags

      if [ -n "$BRANCH" ]; then
        # Ensure we're on the desired branch
        git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH" || exit 1
        git pull --ff-only || warn "Could not fast-forward ${REPO_NAME} on branch $BRANCH"
      else
        git pull || warn "Failed to update ${REPO_NAME} (continuing with existing state)"
      fi
    ) && ok "Updated ${REPO_NAME}" \
      || warn "Failed to update ${REPO_NAME} (continuing with existing state)"
  fi

  # Install into virtual environment
  log "Installing ${REPO_NAME} into virtual environment"
  (
    cd "$REPO_PATH" || exit 1
    "$VENV_DIR/bin/python" -m pip install .
  ) && ok "Installed ${REPO_NAME} into virtual environment" \
    || { error "Failed to install ${REPO_NAME} into virtual environment"; return 1; }
}

# Install non-python repository by setup.sh
install_repo() {

  # Arguments
  local REPO_URL="$1"
  local TARGET_DIR="$2"
  local LABEL="$3"
  local BRANCH="${4:-}"
  local SETUP_REL="${5:-}"
  local USE_YES="${6:-}"

  log "Installing ${LABEL} from ${REPO_URL}${BRANCH:+ (branch: $BRANCH)}..."

  # Ensure directory exists
  log "Preparing directory: $TARGET_DIR"
  mkdir -p "$TARGET_DIR" \
    && ok "Directory ready: $TARGET_DIR" \
    || { error "Failed to create directory: $TARGET_DIR"; return 1; }

  # Clone or update
  if [ ! -d "$TARGET_DIR/.git" ]; then
    log "Cloning into: $TARGET_DIR"
    if [ -n "$BRANCH" ]; then
      git clone --single-branch --branch "$BRANCH" "$REPO_URL" "$TARGET_DIR" \
        && ok "Cloned ${LABEL} (branch: $BRANCH)" \
        || { error "Failed to clone ${LABEL} (branch: $BRANCH)"; return 1; }
    else
      git clone "$REPO_URL" "$TARGET_DIR" \
        && ok "Cloned ${LABEL}" \
        || { error "Failed to clone ${LABEL}"; return 1; }
    fi
  else
    log "Repo already exists: $TARGET_DIR (updating)"
    (
      cd "$TARGET_DIR" || exit 1
      git fetch --all --tags

      if [ -n "$BRANCH" ]; then
        git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH" || exit 1
        git pull --ff-only || warn "Could not fast-forward ${LABEL} on branch $BRANCH"
      else
        git pull || warn "Failed to update ${LABEL} (continuing with existing state)"
      fi
    ) && ok "Updated ${LABEL}" \
      || warn "Failed to update ${LABEL} (continuing with existing state)"
  fi

  # Run setup script from inside directory
  if [ -n "$SETUP_REL" ]; then
    local SETUP_PATH="$TARGET_DIR/$SETUP_REL"

    if [ -f "$SETUP_PATH" ]; then
      log "Running setup script: $SETUP_PATH"

      if [ "$USE_YES" = "yes" ]; then
        ( cd "$TARGET_DIR" && yes | bash "./$SETUP_REL" ) \
          && ok "${LABEL} setup completed" \
          || warn "${LABEL} setup returned an error (check output)"
      else
        ( cd "$TARGET_DIR" && bash "./$SETUP_REL" ) \
          && ok "${LABEL} setup completed" \
          || warn "${LABEL} setup returned an error (check output)"
      fi
    else
      warn "Setup script not found: $SETUP_PATH (skipping)"
    fi
  else
    log "No setup script specified for ${LABEL} (skipping setup step)"
  fi

  # Set ownership and permissions
  log "Setting ownership and permissions for ${LABEL}"
  chown -R pi:pi "$TARGET_DIR" \
    && ok "Ownership set to pi:pi for ${LABEL}" \
    || warn "Failed to set ownership for ${LABEL}"

  chmod +x "$TARGET_DIR"/* 2>/dev/null \
    && ok "Executable bit set on files in $TARGET_DIR" \
    || warn "Could not chmod +x some files in $TARGET_DIR (may be normal if directories exist)"
}

# Checks if line has already been added to file and writes if not
ensure_line() {

  # Arguments
  local LINE="$1"
  local FILE="$2"

  # If line is already present in file, then skip, else append and warn if fails
  if grep -Fxq "$LINE" "$FILE"; then
    log "Already present in $(basename "$FILE"): $LINE"
  else
    echo "$LINE" >> "$FILE" \
      && ok "Added to $(basename "$FILE"): $LINE" \
      || warn "Failed to add to $(basename "$FILE"): $LINE"
  fi
}

# ----- WIFI & STATIC IP CONFIGURATION -----

log "Configuring WiFi profiles (suffix: ${SUFFIX})..."

# Pass in suffix argument for IP address and broadcast WiFi SSID stated when executing setup script (should be based on meltstake number e.g. 01, 02, 03)
SUFFIX="${1:-01}"

log "Static IP addresses will use XX = ${SUFFIX}"

log "Cleaning up any existing NetworkManager WiFi profiles"

# Remove redundancies before creating new connections
cleanup_con "wifi-mixz"
cleanup_con "wifi-ScienceShare"
cleanup_con "ap-meltStake${SUFFIX}"
cleanup_con "wifi-meltStake${SUFFIX}"
cleanup_con "netplan-wlan0-mixz"

# Create Network Manager WiFi profile for mixz 
log "Creating WiFi profile: wifi-mixz (SSID: mixz)"
sudo nmcli con add type wifi ifname wlan0 \
  con-name "wifi-mixz" \
  ssid "mixz" >/dev/null 2>&1 \
  && ok "Created connection: wifi-mixz" \
  || error "Failed to create connection: wifi-mixz"

# Set password for mixz
log "Setting WPA2 password for wifi-mixz"
sudo nmcli con mod "wifi-mixz" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "chi10eps9"

# Assign static IP for mixz
log "Assigning static IP 10.0.1.1${SUFFIX}/24 to wifi-mixz"
sudo nmcli con mod "wifi-mixz" ipv4.method manual \
  ipv4.addresses "10.0.1.1${SUFFIX}/24" \
  ipv4.gateway "10.0.1.1" \
  ipv4.dns "10.0.1.1" \
  ipv4.ignore-auto-dns yes \
  && ok "Static IP configured for wifi-mixz"

# Enable autoconnect and set priority for mixz (30)
log "Enabling autoconnect (priority 30) for wifi-mixz"
sudo nmcli con mod "wifi-mixz" connection.autoconnect yes connection.autoconnect-priority 30

# Create Network Manager WiFi profile for ScienceShare
log "Creating WiFi profile: wifi-ScienceShare (SSID: ScienceShare)"
sudo nmcli con add type wifi ifname wlan0 \
  con-name "wifi-ScienceShare" \
  ssid "ScienceShare" >/dev/null 2>&1 \
  && ok "Created connection: wifi-ScienceShare" \
  || error "Failed to create connection: wifi-ScienceShare"

# Set password for ScienceShare
log "Setting WPA2 password for wifi-ScienceShare"
sudo nmcli con mod "wifi-ScienceShare" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "SwimRobotSwim"

# Assign static IP for ScienceShare
log "Assigning static IP 192.168.0.1${SUFFIX}/24 to wifi-ScienceShare"
sudo nmcli con mod "wifi-ScienceShare" ipv4.method manual \
  ipv4.addresses "192.168.0.1${SUFFIX}/24" \
  ipv4.gateway "192.168.0.1" \
  ipv4.dns "192.168.0.1" \
  ipv4.ignore-auto-dns yes \
  && ok "Static IP configured for wifi-ScienceShare"

# Enable autoconnect and set priority for ScienceShare (20)
log "Enabling autoconnect (priority 20) for wifi-ScienceShare"
sudo nmcli con mod "wifi-ScienceShare" connection.autoconnect yes connection.autoconnect-priority 20

# Configure fallback AccessPoint meltstakeXX
log "Configuring fallback Access Point: meltStake${SUFFIX}"
log "This AP will activate only if no known WiFi is available"
sudo nmcli con add type wifi ifname wlan0 \
  con-name "ap-meltStake${SUFFIX}" \
  ssid "meltStake${SUFFIX}" \
  autoconnect yes \
  connection.autoconnect-priority -10 \
  && ok "Created fallback AP profile: ap-meltStake${SUFFIX}"

# Modify connection to Access Point (AP) mode, 2.4 GHz band channel 6, with password: raspberry
log "Setting AP mode (2.4 GHz, channel 6) and WPA2 password"
sudo nmcli con mod "ap-meltStake${SUFFIX}" \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  802-11-wireless.channel 6 \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "raspberry" \
  && ok "Access Point configuration complete"

# End of section summary for WiFi & static IP configuration
echo
ok "WiFi configuration complete"
log "Configured networks:"
log "wifi-mixz → 10.0.1.1${SUFFIX}"
log "wifi-ScienceShare → 192.168.0.1${SUFFIX}"
log "ap-meltStake${SUFFIX} (fallback AP)"
echo

# ----- MAKE DIRECTORIES AND FILES FOR DATA LOGGING -----

log "Initializing data logging directory..."

# Set data directory path
DATA_DIR="/home/pi/data"

# Create data directory
if mkdir -p "$DATA_DIR"; then
  ok "Data directory ready: $DATA_DIR"
else
  error "Failed to create data directory: $DATA_DIR"
fi

# Data file names
dat_files=(
  "meltstake.log"
  "Battery.dat"
  "Orientation.dat"
  "Rotations.dat"
  "Pressure.dat"
  "Ping.dat"
)

log "Creating data log files"

# Create each file inside of data directory
for filename in "${dat_files[@]}"; do
  if touch "$DATA_DIR/$filename"; then
    ok "Initialized file: $DATA_DIR/$filename"
  else
    warn "Failed to create file: $DATA_DIR/$filename"
  fi
done

# Set ownership
log "Setting ownership to pi:pi"
if chown -R pi:pi "$DATA_DIR"; then
  ok "Ownership set to pi:pi"
else
  warn "Failed to set ownership on $DATA_DIR"
fi

# Set permissions
log "Setting directory and file permissions"
chmod 755 "$DATA_DIR" && chmod 644 "$DATA_DIR"/* \
  && ok "Permissions applied to each file in $DATA_DIR" \
  || warn "Failed to apply one or more permissions"

# ----- PYTHON ENVIRONMENT SETUP -----

log "Updating OS packages, installing system level dependencies, and setting up virtual environment..."

# Update OS packages
log "Updating OS package index"
apt-get update && ok "OS package index updated"

# Install system-level depedencies
log "Installing system-level dependencies"
apt-get install -y \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  python3-setuptools \
  python3-wheel \
  build-essential \
  swig \
  git \
  i2c-tools \
  python3-smbus2 \
  liblgpio1 \
  liblgpio-dev \
  && ok "System dependencies installed"

# Project paths
PROJECT_DIR="/home/pi/MeltStake-Pi5"
VENV_DIR="$PROJECT_DIR/venv"
REQ_FILE="$PROJECT_DIR/requirements.txt"

# Ensure project directory exists
mkdir -p "$PROJECT_DIR"

# Create virtual environment if missing
if [ ! -d "$VENV_DIR" ]; then
  log "Creating Python virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR" \
    && ok "Virtual environment created"
else
  log "Virtual environment already exists (skipping creation)"
fi

# Upgrade pip inside virtual environment
log "Upgrading pip inside virtual environment"
"$VENV_DIR/bin/python" -m pip install --upgrade pip \
  && ok "pip upgraded inside virutal environment"

# Set virtual environment ownership
chown -R pi:pi "$PROJECT_DIR"

# Bootstrap build tools (pip/setuptools/wheel) to virtual environment
log "Ensuring virtual environment build tools are present (pip/setuptools/wheel)"
"$VENV_DIR/bin/python" -m pip install -U pip setuptools wheel \
  && ok "virtual environment build tools ready" \
  || warn "Could not upgrade virtual environment build tools (continuing, but installs may fail)"

# Install Python dependencies inside of virtual environment
if [ -f "$REQ_FILE" ]; then
  log "Installing Python dependencies from requirements.txt into virtual environment"
  "$VENV_DIR/bin/pip" install -r "$REQ_FILE" \
    && ok "Python dependencies installed into virtual environment"
else
  warn "requirements.txt not found at $REQ_FILE (skipping Python dependencies)"
fi

# GPIO backend fix for circuitpython
log "Ensuring Pi 5-compatible GPIO backend (remove legacy RPi.GPIO, install rpi-lgpio)"

"$VENV_DIR/bin/python" -m pip uninstall -y RPi.GPIO \
  && ok "Removed RPi.GPIO from virtual environment" \
  || warn "RPi.GPIO not installed in virtual environment (skipping uninstall)"

"$VENV_DIR/bin/python" -m pip install -U rpi-lgpio \
  && ok "Installed/updated rpi-lgpio in virtual environment" \
  || { warn "Failed to install rpi-lgpio"; exit 1; }

# Print virtual environment Python version
log "Python version in virutal environment:"
"$VENV_DIR/bin/python" --version

# ----- INSTALL BLUE ROBOTICS SOFTWARE PACKAGES ----

log "Installing Software Packages from GitHub..."

# Create package directory
PKG_DIR="/home/pi/packages"
log "Preparing package directory: $PKG_DIR"
mkdir -p "$PKG_DIR" && ok "Package directory ready"

# Navigator IMU
install_python_repo \
  "https://github.com/bluerobotics/icm20602-python" \
  "icm20602-python" "IMU Software" \
  || warn "Continuing after failure: icm20602-python"

# Navigator Magnetometer
install_python_repo \
  "https://github.com/bluerobotics/mmc5983-python" \
  "mmc5983-python" "Magnetometer Software" \
  || warn "Continuing after failure: mmc5983-python"

# Navigator Logging
install_python_repo \
  "https://github.com/bluerobotics/llog-python" \
  "llog-python" \
  "Logging Utilities" \
  || warn "Continuing after failure: llog-python"

# Blue Robotics PCA PWM Driver
install_python_repo \
  "https://github.com/bluerobotics/pca9685-python" \
  "pca9685-python" "PCA9685 PWM Controller" \
  || warn "Continuing after failure: pca9685-python"

# Blue Robotics ADS1115 Analog-Digital-Converter
install_python_repo \
  "https://github.com/bluerobotics/ads1115-python" \
  "ads1115-python" "ADS1115 ADC" \
  || warn "Continuing after failure: ads1115-python"

# Blue Robotics MS5387 Bar30 Pressure/Temperature Sensor
install_python_repo \
  "https://github.com/bluerobotics/ms5837-python" \
  "ms5837-python" "MS5387 Bar30 Pressure/Temperature Sensor" \
  || warn "Continuing after failure: ms5837-python"

# Blue Robotics Ping Sonar
install_python_repo \
  "https://github.com/bluerobotics/ping-python.git" \
  "ping-python" \
  "Blue Robotics Ping Sonar" \
  "deployment" \
  || warn "Continuing after failure: ms5837-python"

# Camera Capture
install_repo \
  "https://github.com/noahaosman/camera_capture.git" \
  "/home/pi/camera_capture" \
  "camera_capture" \
  "MeltStakes" \
  "setup.sh" \
  "yes" \
  || warn "Continuing after failure: camera_capture"

# Acoustic Beacon
install_repo \
  "https://github.com/noahaosman/acoustic-beacons.git" \
  "/home/pi/nav" \
  "acoustic-beacons" \
  "jasmine" \
  "install.sh" \
  "" \
  || warn "Continuing after failure: acoustic-beacons"

BEACON_SERVICE="/etc/systemd/system/beacons.service"

# Patch + enable service only if the unit file exists
if [ -f "$BEACON_SERVICE" ]; then
  log "Configuring systemd restart policy for beacons.service"

  # Only insert Restart lines if they're not already present
  if ! grep -q '^Restart=always' "$BEACON_SERVICE"; then
    if grep -q 'StandardOutput=syslog' "$BEACON_SERVICE"; then
      sed -i '/StandardOutput=syslog/ i Restart=always\
RestartSec=30' "$BEACON_SERVICE" \
        && ok "Added Restart=always and RestartSec=30 to beacons.service" \
        || warn "Failed to patch beacons.service"
    else
      warn "Anchor 'StandardOutput=syslog' not found; appending restart policy to end of service file"
      {
        echo ""
        echo "Restart=always"
        echo "RestartSec=30"
      } >> "$BEACON_SERVICE" \
        && ok "Appended restart policy to beacons.service" \
        || warn "Failed to append restart policy to beacons.service"
    fi
  else
    log "Restart policy already present (skipping patch)"
  fi

  log "Reloading systemd daemon"
  systemctl daemon-reload && ok "systemd daemon reloaded" || warn "daemon-reload failed"

  log "Enabling beacons service at boot"
  systemctl enable beacons && ok "beacons enabled" || warn "Failed to enable beacons"
else
  warn "beacons.service not found at $BEACON_SERVICE; skipping systemd patch/enable"
fi

# ----- CONFIGURE INTERFACE OPTIONS -----

log "Configuring Raspberry Pi interface options"
log "Editing boot configuration file: /boot/firmware/config.txt"
log "NOTE: Changes below require a reboot to take effect"

INTERFACE_FILE="/boot/firmware/config.txt"

# Disable Bluetooth
log "Disabling onboard Bluetooth"
ensure_line "dtoverlay=disable-bt" "$INTERFACE_FILE"

# Enable Primary UART
log "Enabling primary UART"
ensure_line "enable_uart=1" "$INTERFACE_FILE"

# Enable SPI1 Controller with 3 chip select lines
log "Enabling SPI1 controller with 3 chip-select lines"
ensure_line "dtoverlay=spi1-3cs" "$INTERFACE_FILE"

# Enable I2C Controller
log "Enabling hardware I2C controller"
ensure_line "dtparam=i2c_arm=on" "$INTERFACE_FILE"

# Enable Software I2C Bus 1 on Pins 2 and 3
log "Enabling software I2C bus 1 on GPIO pins 2 (SDA) and 3 (SCL)"
ensure_line "dtoverlay=i2c-gpio,bus=1,i2c_gpio_sda=2,i2c_gpio_scl=3" "$INTERFACE_FILE"

# Enable Software I2C Bus 4 on Pins 6 and 7
log "Enabling software I2C bus 4 on GPIO pins 6 (SDA) and 7 (SCL)"
ensure_line "dtoverlay=i2c-gpio,bus=4,i2c_gpio_sda=6,i2c_gpio_scl=7,baudrate=1000000" "$INTERFACE_FILE"

# Real Time Clock on Pins 22 and 23
log "Configuring DS3231 RTC on GPIO pins 22 (SDA) and 23 (SCL)"
ensure_line "dtoverlay=i2c-rtc-gpio,ds3231,i2c_gpio_sda=22,i2c_gpio_scl=23" "$INTERFACE_FILE"

# Enables userspace access to I²C buses via /dev/i2c-*
log "Enabling userspace I2C device access (/dev/i2c-*)"
ensure_line "i2c-dev" /etc/modules

# Enable hardware serial port
log "Enabling hardware serial interface via raspi-config"
if raspi-config nonint do_serial_hw 0; then
  ok "Hardware serial enabled"
else
  warn "Failed to enable hardware serial"
fi

# Enable I2C
log "Enabling I2C interface via raspi-config"
if raspi-config nonint do_i2c 0; then
  ok "I2C interface enabled"
else
  warn "Failed to enable I2C interface"
fi

log "Interface configuration complete (reboot required)"

# ----- DIRECTORY PARAMETERS -----
sudo chown -R pi:pi $PROJECT_DIR

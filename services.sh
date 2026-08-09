#!/usr/bin/sudo bash

SERVICES=(
    meltstake
    beacons
    heartbeat
    LeakDetection
    ptv
    sonar
    ctd
)


if [ "$1" == "status" ]; then
    format_age() {
        local seconds="$1"

        if (( seconds < 60 )); then
            echo "${seconds}s ago"
        elif (( seconds < 3600 )); then
            echo "$((seconds / 60))min ago"
        elif (( seconds < 86400 )); then
            echo "$((seconds / 3600))h ago"
        else
            echo "$((seconds / 86400))d ago"
        fi
    }
    printf "%-15s %-10s %-15s %-35s\n" "SERVICE" "ENABLED" "STATUS" "STARTED"
    for svc in "${SERVICES[@]}"; do
        enabled=$(systemctl is-enabled "$svc" 2>/dev/null)
        status=$(systemctl is-active "$svc" 2>/dev/null)

        age_timestamp=$(systemctl show "$svc" -p ActiveEnterTimestamp --value 2>/dev/null)

        if [[ -n "$age_timestamp" ]]; then
            start_epoch=$(date -d "$age_timestamp" +%s 2>/dev/null)
            now_epoch=$(date +%s)
            age="$age_timestamp; $(format_age $((now_epoch - start_epoch)))"
        else
            age=" "
        fi

        printf "%-15s %-10s %-15s %-35s\n" "$svc" "$enabled" "$status" "$age"
    done

elif [ "$1" == "disable" ]; then
    for svc in "${SERVICES[@]}"; do
        echo "Disabling $svc..."
        systemctl disable "$svc"
    done

elif [ "$1" == "enable" ]; then
    for svc in "${SERVICES[@]}"; do
        echo "Enabling $svc..."
        systemctl enable "$svc"
    done

elif [ "$1" == "stop" ]; then
    for svc in "${SERVICES[@]}"; do
        echo "Stopping $svc..."
        systemctl stop "$svc"
    done

elif [ "$1" == "start" ]; then
    for svc in "${SERVICES[@]}"; do
        echo "Starting $svc..."
        systemctl start "$svc"
    done

else
    echo "Usage: $0 {status|disable|enable|start|stop}"
fi

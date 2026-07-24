#!/bin/bash
echo "Restarting AITC services..."
bash ~/AITC/stop.sh
sleep 2
bash ~/AITC/start.sh

#!/bin/sh
# Start nginx
service nginx start

# Start Flask app in background
python app.py &

# tail nginx logs to keep container alive
tail -f /var/log/nginx/error.log

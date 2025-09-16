#!/bin/bash
sed -u 's/^Host:.*/Host: 127.0.0.1/i' | socat - TCP:127.0.0.1:8222
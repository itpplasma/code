#!/bin/bash
cd base 
./cross_build_push.sh 
sleep 10
cd ../devel 
./cross_build_push.sh
sleep 10
cd ../devel-tex
./cross_build_push.sh

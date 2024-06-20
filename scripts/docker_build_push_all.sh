#!/bin/bash
$CODE/images/base/cross_build_push.sh
sleep 10
$CODE/images/devel/cross_build_push.sh
sleep 10
$CODE/images/devel-tex/cross_build_push.sh

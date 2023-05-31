#!/bin/bash

checkmodule -M -m -o docker_bluetooth.mod docker_bluetooth.te
semodule_package -o docker_bluetooth.pp -m docker_bluetooth.mod
semodule -i docker_bluetooth.pp

# Cleanup
rm -rf *.pp *.mod
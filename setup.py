#!/usr/bin/env python

from setuptools import find_packages, setup

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='ble2mqtt',
    version='0.1.0a6',
    description='BLE to MQTT bridge',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author='Ivan Belokobylskiy',
    author_email='belokobylskij@gmail.com',
    url='https://github.com/devbis/ble2mqtt/',
    entry_points={
        'console_scripts': ['ble2mqtt=ble2mqtt:main']
    },
    packages=find_packages(include=['ble2mqtt', 'ble2mqtt.*']),
    install_requires=[
        'aio-mqtt>=0.2.0',
        'bleak>=0.9.0',
    ],
    classifiers=[
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Utilities',
    ],
)

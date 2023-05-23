#!/usr/bin/env python

from setuptools import find_packages, setup

from ble2mqtt.__version__ import VERSION

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='ble2mqtt',
    version=VERSION,
    description='BLE to MQTT bridge',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author='Ivan Belokobylskiy',
    author_email='belokobylskij@gmail.com',
    url='https://github.com/devbis/ble2mqtt/',
    entry_points={
        'console_scripts': ['ble2mqtt=ble2mqtt.__main__:main']
    },
    packages=find_packages(include=['ble2mqtt', 'ble2mqtt.*']),
    install_requires=[
        'aio-mqtt-mod>=0.3.0',
        'bleak>=0.12.0',
    ],
    extras_require={
        'full': ['pycryptodome']
    },
    classifiers=[
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Utilities',
    ],
)

#!/usr/bin/env python

from subprocess import getoutput

from setuptools import find_packages, setup
from setuptools.command.install import install

from ble2mqtt.__version__ import VERSION


class PostInstall(install):
    pkgs = ' http://github.com/hbldh/bleak/tarball/dbus-next-2#egg=bleak-0.11.0a1'

    def run(self):
        install.run(self)
        print(getoutput('python3 -m pip install' + self.pkgs))


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
    cmdclass={'install': PostInstall},
    install_requires=[
        'aio-mqtt>=0.2.0',
        # 'bleak @ http://github.com/hbldh/bleak/tarball/dbus-next-2#egg=bleak-0.11.0a1',
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

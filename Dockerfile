# First stage, build requirements
FROM python:slim as builder

RUN apt-get update && \
    apt-get install gcc git -y && \
    apt-get clean

WORKDIR /usr/src/app

# To speed up consecutive builds, copy only requirements and install them
COPY ./requirements.txt .

# Install requirements and ignore warnings for local installation
RUN pip install --user --no-warn-script-location -r requirements.txt

COPY . .

RUN pip install --user --no-warn-script-location ble2mqtt

# Second stage
FROM python:slim as app

# Bluetoothctl is required
RUN apt-get update && \
    apt-get install bluez -y && \
    apt-get clean

# Copy the local python packages
COPY --from=builder /root/.local /root/.local

ENV PATH=/root/.local/bin:$PATH

CMD [ "ble2mqtt" ]

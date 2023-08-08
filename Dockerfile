# First stage, build requirements
FROM python:3-slim as builder

RUN apt-get update && \
    apt-get install gcc git -y && \
    apt-get clean

WORKDIR /usr/src/app

# To speed up consecutive builds, copy only requirements and install them
COPY . .

# Install requirements and ignore warnings for local installation
RUN pip install --user --no-warn-script-location -r requirements.txt

RUN pip install --user --no-warn-script-location .

# Second stage
FROM python:3-slim as app

ENV ROOTLESS_UID 1001
ENV ROOTLESS_GID 1001
ENV ROOTLESS_NAME "rootless"

# Bluetoothctl is required
RUN apt-get update && \
    apt-get install bluez -y && \
    apt-get clean

# Copy the local python packages
RUN groupadd --gid ${ROOTLESS_GID} ${ROOTLESS_NAME} && \
    useradd --gid ${ROOTLESS_GID} --uid ${ROOTLESS_UID} -d /home/${ROOTLESS_NAME} ${ROOTLESS_NAME}

COPY --from=builder /root/.local /home/${ROOTLESS_NAME}/.local

# Copy run script
COPY ./docker_entrypoint.sh /home/${ROOTLESS_NAME}/docker_entrypoint.sh
RUN chmod +x /home/${ROOTLESS_NAME}/docker_entrypoint.sh
RUN chown -R ${ROOTLESS_UID}:${ROOTLESS_GID} /home/${ROOTLESS_NAME}

ENV PATH=/home/rootless/.local/bin:$PATH

USER ${ROOTLESS_NAME}
CMD [ "/home/rootless/docker_entrypoint.sh" ]

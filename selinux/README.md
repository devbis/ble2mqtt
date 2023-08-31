# SELinux Policy for bluetooth usage via docker


## Get SELinux logs

If you are not sure that SELinux may be blocking your bluetooth access via the docker container, query the SELinux logs using `ausearch`:

```
ausearch -m AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR -ts recent
```

For example, this may be a possible output in case that SELinux is denying access to bluetooth:

```
type=USER_AVC msg=audit(1685511424.957:14363): pid=838 uid=81 auid=4294967295 ses=4294967295 subj=system_u:system_r:system_dbusd_t:s0-s0:c0.c1023 msg='avc:  denied  { send_msg } for  scontext=system_u:system_r:bluetooth_t:s0 tcontext=system_u:system_r:spc_t:s0 tclass=dbus permissive=0 exe="/usr/bin/dbus-broker" sauid=81 hostname=? addr=? terminal=?'
```

This AVC denial error will make ble2mqtt to restart bluetooth over and over again (using `hciconfig` - deprecated), giving this (misleading) error:

```
Can't open HCI socket.: Address family not supported by protocol
```


## Create SELinux policy

This will create a modular policy file using a Type Enforcement (TE) file as input.

Remember to run this script only as root user, in the host side (not in the container).

```
./apply.docker-bluetoth-policy.sh
```

After applying the SELinux policy using `apply.docker-bluetoth-policy.sh`, make sure to restart `ble2mqtt` container.


### Recreate the SELinux policy

If you want to create the TE file yourself, use `audit2allow`, which will create a policy file that will overcome your AVC errors.

```
ausearch -m AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR -ts recent | audit2allow -M policy
```
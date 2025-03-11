#!/bin/bash

# Run this on your host machine to prepare FRR directories
mkdir -p /etc/frr/r0_0 /etc/frr/r0_1 /etc/frr/r0_2 /etc/frr/r0_3 /etc/frr/r0_4 /etc/frr/r1_0 /etc/frr/r1_1 /etc/frr/r1_2 /etc/frr/r1_3 /etc/frr/r1_4 /etc/frr/g_nyc /etc/frr/g_cbr /etc/frr/g_lon /etc/frr/g_lax /etc/frr/v_cargo1 /etc/frr/v_cargo2

# Create basic FRR config in each directory
for dir in /etc/frr/r*/ /etc/frr/g_*/ /etc/frr/v_*/; do
  # Create daemons file
  echo "zebra=yes
bgpd=no
ospfd=yes
ospf6d=no
ripd=no
ripngd=no
isisd=no
pimd=no
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=no
fabricd=no
vrrpd=no
staticd=yes" > "$dir/daemons"

  # Create basic zebra.conf
  echo "hostname $(basename $dir)
log file /etc/frr/zebra.log
!
ip forwarding" > "$dir/zebra.conf"

  # Create basic frr.conf
  echo "hostname $(basename $dir)
log file /etc/frr/frr.log
!
service integrated-vtysh-config" > "$dir/frr.conf"

  # Set permissions
  chmod -R 777 "$dir"
done

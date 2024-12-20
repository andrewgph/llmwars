#!/bin/bash

SCRIPT_DIR=$(dirname "$0")

VM_NAME="ubuntu-server-24"
DISK_FILE="$SCRIPT_DIR/qemu_vm_files/ubuntu-vm.qcow2"
ISO_FILE="$SCRIPT_DIR/qemu_vm_files/cloud-init.iso"
RAM="4G"
CPUS=2

# Download the cloud image
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-arm64.img -O "$DISK_FILE"

# Resize the disk (cloud images are minimal by default)
qemu-img resize "$DISK_FILE" 20G

# Generate SSH key pair if it doesn't exist
SSH_KEY_PATH="$SCRIPT_DIR/qemu_vm_files/vm_key"
if [ ! -f "$SSH_KEY_PATH" ]; then
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_PATH" -N "" -C "vm-access-key"
fi

# Read the public key
PUBLIC_KEY=$(cat "${SSH_KEY_PATH}.pub")

# Create a temporary user-data file with the SSH key
sed "s|ssh-rsa.*|$PUBLIC_KEY|" "$SCRIPT_DIR/qemu_vm_config/user-data" > "$SCRIPT_DIR/qemu_vm_config/user-data.tmp"
mv "$SCRIPT_DIR/qemu_vm_config/user-data.tmp" "$SCRIPT_DIR/qemu_vm_config/user-data"

# Create a function/alias for cloud-localds using Docker
cloud-localds() {
    docker run --rm \
        -v "$SCRIPT_DIR:/data" \
        ubuntu:latest \
        bash -c "apt-get update && apt-get install -y cloud-image-utils && cloud-localds /data/$1 /data/$2 /data/$3"
}

rm "$ISO_FILE"
cloud-localds "$ISO_FILE" qemu_vm_config/user-data qemu_vm_config/meta-data

qemu-system-aarch64 \
    -name "$VM_NAME" \
    -machine virt \
    -accel hvf \
    -cpu cortex-a72 \
    -smp "$CPUS" \
    -m "$RAM" \
    -bios /opt/homebrew/share/qemu/edk2-aarch64-code.fd \
    -drive if=virtio,file="$DISK_FILE" \
    -cdrom "$ISO_FILE" \
    -device virtio-net-pci,netdev=net0 \
    -netdev user,id=net0,hostfwd=tcp::2224-:22 \
    -device virtio-serial \
    -chardev socket,path=/tmp/qga.sock,server=on,wait=off,id=qga0 \
    -device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0 \
    -nographic
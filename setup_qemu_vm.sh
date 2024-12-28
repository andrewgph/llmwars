#!/bin/bash

SCRIPT_DIR=$(dirname "$0")

VM_NAME="ubuntu-server-24"
QEMU_VM_DIR="$SCRIPT_DIR/qemu_vm_files"
DISK_FILE="$QEMU_VM_DIR/ubuntu-vm.qcow2"
ISO_FILE="$QEMU_VM_DIR/cloud-init.iso"
RAM="4G"
CPUS="4"
LOCAL_SSH_PORT="2224"
LOG_FILE="$QEMU_VM_DIR/vm_setup.log"

# Download the cloud image
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-arm64.img -O "$DISK_FILE"

# Resize the disk (cloud images are minimal by default)
qemu-img resize "$DISK_FILE" 20G

# Generate SSH key pair if it doesn't exist
SSH_KEY_PATH="$QEMU_VM_DIR/vm_key"
if [ ! -f "$SSH_KEY_PATH" ]; then
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_PATH" -N "" -C "vm-access-key"
fi

# Read the public key
PUBLIC_KEY=$(cat "${SSH_KEY_PATH}.pub")

# Create a new user-data file with the SSH key
USER_DATA_FILE="$QEMU_VM_DIR/user-data"
sed "s|ssh-rsa.*|$PUBLIC_KEY|" "$SCRIPT_DIR/qemu_vm_config/user-data" > "$USER_DATA_FILE"

# Create a function/alias for cloud-localds using Docker
cloud-localds() {
    docker run --rm \
        -v "$SCRIPT_DIR:/data" \
        ubuntu:latest \
        bash -c "apt-get update && apt-get install -y cloud-image-utils && cloud-localds /data/$1 /data/$2 /data/$3"
}

rm "$ISO_FILE"
cloud-localds "$ISO_FILE" "$USER_DATA_FILE" "$SCRIPT_DIR/qemu_vm_config/meta-data"

# Function to cleanup VM on script exit
cleanup() {
    if [ -n "$VM_PID" ]; then
        echo "Shutting down VM..."
        kill $VM_PID
        wait $VM_PID 2>/dev/null
        echo "VM shutdown complete"
    fi
}

vm_ssh() {
    ssh -q \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=60 \
        -o ServerAliveCountMax=10 \
        -i "$QEMU_VM_DIR/vm_key" \
        -p "$LOCAL_SSH_PORT" myuser@localhost "$@"
}

# Start the VM in the background and save its PID
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
    -netdev user,id=net0,hostfwd=tcp::"$LOCAL_SSH_PORT"-:22 \
    -device virtio-serial \
    -chardev socket,path=/tmp/qga.sock,server=on,wait=off,id=qga0 \
    -device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0 \
    -nographic > "$LOG_FILE" 2>&1 &
VM_PID=$!

# Set trap to ensure VM is stopped when script exits
trap cleanup EXIT

# Wait for VM to boot and SSH to become available
echo "Waiting for VM to boot..."
MAX_RETRIES=60  # Maximum number of retries (e.g., 5 minutes with 5-second sleep)
RETRY_COUNT=0
while ! vm_ssh "systemctl is-system-running" >/dev/null 2>&1; do
    sleep 5
    RETRY_COUNT=$((RETRY_COUNT + 1))
    
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "Error: VM failed to boot within timeout period"
        exit 1
    fi
    
    echo "Still waiting for VM to boot... (attempt $RETRY_COUNT/$MAX_RETRIES)"
done
echo "VM completed cloud-init initialization"
import paramiko

# Router connection details.
router_ip = "10.53.16.109"      # Replace with your router's IP address.
username = "admin"     # Replace with your router login username.
password = "password"     # Replace with your router login password.
port = 22                      # Default SSH port.

def get_firmware_version(output):
    """
    Parse the router output to extract the firmware version.
    Expected line format:
      Firmware name:      18s.1.03f
    """
    for line in output.splitlines():
        if "Firmware name:" in line:
            # Split the line by ':' and trim extra spaces.
            parts = line.split(":", 1)
            if len(parts) > 1:
                return parts[1].strip()
    return None

def main():
    # Create an SSH client and set up to auto-accept unknown host keys (for demo purposes).
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # Connect to the router.
        ssh_client.connect(router_ip, port=port, username=username, password=password)
        print("SSH connection successful.")
        
        # Execute the "show ver" command.
        stdin, stdout, stderr = ssh_client.exec_command("show ver")
        output = stdout.read().decode("utf-8")
        
        # Extract the firmware version from the command output.
        firmware_version = get_firmware_version(output)
        if firmware_version:
            print("Firmware version is:", firmware_version)
        else:
            print("Firmware version not found in the output.")
    except Exception as e:
        print("An error occurred:", e)
    finally:
        ssh_client.close()

if __name__ == "__main__":
    main()

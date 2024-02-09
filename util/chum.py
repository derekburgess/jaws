from rich.progress import Progress
import socket
import ssl
import random
import time

# Used in conjection with listener.py and observed in sea.py
# Simulates file exfiltration for the configuration below. Interactive! Spared no expense...
# This is useful for testing your detection capabilities.
# https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-linux-inst-ssh.html
# ssh -i /path/key-pair-name.pem ec2-user@instance-public-dns-name
# Port 53 (DNS): Hackers may use DNS tunneling to bypass security measures, as DNS traffic is usually allowed through firewalls.
# Port 80 (HTTP): Though it's a common port, hackers might disguise their activities within regular web traffic to avoid suspicion.
# Port 53/UDP (DNS UDP): Similar to DNS on TCP, DNS on UDP can also be exploited for data exfiltration.
# Port 8080 (HTTP Proxy): Attackers can use HTTP proxy servers to send data, as many organizations allow outgoing traffic on this port.
# Port 22 (SSH): While SSH is typically used for secure remote access, attackers can tunnel data through SSH sessions.
# Port 123 (NTP): Network Time Protocol (NTP) traffic can be leveraged for covert data transfer due to its typically open nature.
# Port 445 (SMB): Exploiting vulnerabilities in Server Message Block (SMB) protocol can lead to data exfiltration.
# Port 6660-6669 (Internet Relay Chat - IRC): IRC ports are sometimes used for covert communication.
# Port 25 (SMTP): Hackers can use email protocols to exfiltrate data by disguising it as legitimate email traffic.
# Port 3306 (MySQL): If an organization uses MySQL databases, attackers might exploit vulnerabilities to access and exfiltrate data.
# Port 2503 (NMS-DPNSS): NMS-DPNSS is a protocol used for telephony. Attackers can use it to exfiltrate data.
# Port 55553 (Bo2k): Back Orifice 2000 (BO2K) is a remote administration tool. Attackers can use it to exfiltrate data.

HOST = 'AWS DNS'
PORTS = [53, 80, 8080, 445, 6660, 6661, 6662, 6663, 6664, 6665, 6666, 6667, 6668, 6669, 2503, 55553] # Add more ports as needed...
PORT = random.choice(PORTS)
CHUNK_SIZES = [6400, 12800, 25600, 51200]
CHUNK_SIZE = random.choice(CHUNK_SIZES)

def generate_random_data(size):
    return bytes([random.randint(0, 255) for _ in range(size)])

def main():
    mock_file_size_MB = float(input("\nEnter a file size in MB: "))
    MOCK_FILE_SIZE = int(mock_file_size_MB * 1024 * 1024)
    total_chunks = MOCK_FILE_SIZE // CHUNK_SIZE
    if MOCK_FILE_SIZE % CHUNK_SIZE:
        total_chunks += 1

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        print(f"\nConnecting to {HOST}:{PORT}...")
        sock.connect((HOST, PORT))
        context = ssl._create_unverified_context()
        
        with context.wrap_socket(sock, server_hostname=HOST) as ssock:
            print(f"Connected to {HOST}:{PORT} over TLS")
            
            with Progress() as progress:
                task = progress.add_task("Hold onto your butts...", total=total_chunks)
                
                for i in range(total_chunks):
                    chunk_size = CHUNK_SIZE if (i < total_chunks - 1) else (MOCK_FILE_SIZE % CHUNK_SIZE)
                    data = generate_random_data(chunk_size)
                    ssock.sendall(data)
                    progress.update(task, completed=i+1)
                    random_delay = random.uniform(0.01, 0.1)
                    time.sleep(random_delay)
            
            print("File exfiltration simulation complete! Clever girl...")

if __name__ == "__main__":
    main()

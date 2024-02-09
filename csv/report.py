import requests

def get_general_ip_info(ip_address, api_key):
    url = f"https://ipinfo.io/{ip_address}/json"
    headers = {
        'Authorization': f'Bearer {api_key}'
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        return None

if __name__ == "__main__":
    api_key = 'IPINFO KEY'  # Replace with your ipinfo API key
    src_ip_to_query = input("Enter an IP address to search ipinfo: ")
    ip_info = get_general_ip_info(src_ip_to_query, api_key)
    
    if ip_info is not None:
        print("\n")
        for key, value in ip_info.items():
            print(f"{key}: {value}")
        print("\n")

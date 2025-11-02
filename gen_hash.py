from hashlib import sha256


def main():
    password = input("Enter your desired password: ")
    password_sha256 = sha256(password.encode()).hexdigest()
    print(f"Your password hash is: {password_sha256}")

if __name__ == "__main__":
    main()
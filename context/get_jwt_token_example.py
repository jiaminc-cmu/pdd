import asyncio
import os
from pdd.get_jwt_token import get_jwt_token, AuthError, NetworkError, TokenError, UserCancelledError, RateLimitError

# Constants for the CLI application (replace with your actual values)
FIREBASE_API_KEY = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY")  # Your Firebase Web API key
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")  # Your GitHub OAuth App's client ID
APP_NAME = "Prompt Driven Development"  # A unique name for your application

async def main():
    """
    Demonstrates how to use the get_jwt_token function to authenticate with Firebase using GitHub Device Flow.
    """
    print("Starting authentication process...")
    token = None  # Initialize token variable

    try:
        # Attempt to get a valid Firebase ID token
        token = await get_jwt_token(
            firebase_api_key=FIREBASE_API_KEY,
            github_client_id=GITHUB_CLIENT_ID,
            app_name=APP_NAME
        )

        print(f"Authentication successful! Firebase ID token: {token}")
        print("You can now use this token to make authenticated requests to your Firebase backend.")

    except AuthError as e:
        print(f"Authentication failed: {e}")
        if isinstance(e, UserCancelledError):
            print("The authentication process was cancelled by the user.")
        return  # Exit early on auth failure
    except NetworkError as e:
        print(f"Network error: {e}")
        print("Please check your internet connection and try again.")
        return  # Exit early on network failure
    except TokenError as e:
        print(f"Token error: {e}")
        print("There was an issue with token exchange or refresh. Please try re-authenticating.")
        return  # Exit early on token failure
    except RateLimitError as e:
        print(f"Rate limit exceeded: {e}")
        print("Too many authentication attempts. Please try again later.")
        return  # Exit early on rate limit failure

    # Only proceed if we have a valid token
    if token is None:
        print("Failed to obtain token. Exiting.")
        return

    # Replace the JWT_TOKEN in .env with the token generated here
    env_file_path = ".env"
    new_token_line = f"JWT_TOKEN={token}\n"

    # Read the existing lines from the .env file
    if os.path.exists(env_file_path):
        with open(env_file_path, "r") as file:
            lines = file.readlines()
    else:
        lines = []

    # Write the new token to the .env file, replacing the old one if it exists
    with open(env_file_path, "w") as file:
        token_replaced = False
        for line in lines:
            if line.startswith("JWT_TOKEN="):
                file.write(new_token_line)
                token_replaced = True
            else:
                file.write(line)
        if not token_replaced:
            file.write(new_token_line)

    print(f"JWT_TOKEN has been updated in {env_file_path}")

if __name__ == "__main__":
    asyncio.run(main())
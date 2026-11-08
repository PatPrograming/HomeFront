import uvicorn


def main() -> None:
    # Loopback only: the gateway is reached through the local agent and console.
    uvicorn.run("identity_gateway.app:app", host="127.0.0.1", port=8080, reload=False)


if __name__ == "__main__":
    main()

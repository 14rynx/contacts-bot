# Contacts

A simple discord bot to enable your corporation to synchronize contacts alts so
there are no neutrals.

## Setup

Create an `.env` file (copy `.env.example`) which includes a discord bot token and ccp application details, 
as well as your domain name for callbacks for this bot.

Then start the container with
```shell
docker-compose up -d --build contacts
```

This assumes that you run traefik as a reverse-proxy externally.

# Contacts

A simple discord bot to enable your corporation to synchronize contacts alts so
there are no neutrals.

![Example Output](https://i.imgur.com/oDSw3Ot.png)

## Running your own Instance of this Bot

There is no public instance of this bot running as it is corporation or alliance specific. As such you need to run
your own instance, or ask someone to run an instance for you. Since we need to connect to both ESI and discord, the setup is sadly somewhat complicated.
This furtherr assumes that you run a [traefik](https://doc.traefik.io/traefik/) container already for reverse-proxy for docker containers.

TLDR: Create an env file and fill it in with the CCP and Discord info, then run with docker compose.

1. Copy the .env file from the example
    ```shell
    cp .env.example .env
    ```
2. Head over to the [Discord Developers Website](https://discord.com/developers/) and create yourself an application.
    - Go to the "Bot" section and reset the token, then copy the new one. Put it in the .env file (`DISCORD_TOKEN=`).
    - Enable the "Message Content Intent" in the Bot section.
    - Invite your bot to your server in the "OAuth2" section. In the URL Generator, click on "Bot" and then
    further down "Send Messages" and "Read Mesasges/View Channels". Follow the generated URL and add the bot to your server.
    - Add your discord id under `ADMIN` to your .env file.

3. Head over to the [Eve onlone Developers Page](https://developers.eveonline.com/) and create yourself an application.
    - Under "Application Type" select "Authentication & API Access"
    - Under "Permissions" add `esi-corporations.read_structures.v1
    - Under "Callback URL" set `https://yourdomain.com/callback/` (obviously replace your domain)

    Now view the application and copy the values `CCP_REDIRECT_URI`, `CCP_CLIENT_ID` and `CCP_SECRET_KEY` to your .env file.

4. Start the container
    ```shell
    docker-compose up -d --build
    ```

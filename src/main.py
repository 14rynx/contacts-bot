import functools
import logging
import os
import secrets

import discord
from discord.ext import commands
from preston import Preston

from callback_server import callback_server
from models import initialize_database, User, Challenge, Character
from utils import lookup, send_large_message

# Configure the logger
logger = logging.getLogger('discord.main')
logger.setLevel(logging.INFO)

# Initialize the database
initialize_database()

# Setup ESI connection
base_preston = Preston(
    user_agent="Contacts organizing discord bot by larynx.austrene@gmail.com",
    client_id=os.environ["CCP_CLIENT_ID"],
    client_secret=os.environ["CCP_SECRET_KEY"],
    callback_url=os.environ["CCP_REDIRECT_URI"],
    scope="esi-characters.read_contacts.v1 esi-characters.write_contacts.v1",
)

# Setup Discord
intent = discord.Intents.default()
intent.messages = True
intent.message_content = True
bot = commands.Bot(command_prefix='!', intents=intent)


def with_refresh(preston_instance, refresh_token: str):
    new_kwargs = dict(preston_instance._kwargs)
    new_kwargs["refresh_token"] = refresh_token
    new_kwargs["access_token"] = None
    return Preston(**new_kwargs)


def command_error_handler(func):
    """Decorator for handling bot command logging and exceptions."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        ctx = args[0]
        logger.info(f"{ctx.author.name} used !{func.__name__}")

        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in !{func.__name__} command: {e}", exc_info=True)
            await ctx.send(f"An error occurred in !{func.__name__}.")

    return wrapper


def add_contacts(this_character):
    """Add all contacts related with this_character"""

    # Got through registered characters and add this contact
    character_ids = []
    for character in Character.select().where(Character.character_id != this_character.character_id):
        authed_preston = with_refresh(base_preston, character.token)
        authed_preston.post_op(
            'post_characters_character_id_contacts',
            path_data={
                "character_id": character.character_id,
                "standing": 10.0,
                "watched": False,
            },
            post_data=[this_character.character_id],
        )
        character_ids.append(character.character_id)

    # Add contacts to this character
    this_char_authed_preston = with_refresh(base_preston, this_character.token)
    this_char_authed_preston.post_op(
        'post_characters_character_id_contacts',
        path_data={
            "character_id": this_character.character_id,
            "standing": 10.0,
            "watched": False,
        },
        post_data=character_ids,
    )


def remove_contacts(this_character: Character):
    """Remove all contacts related with this_character"""
    this_char_authed_preston = with_refresh(base_preston, this_character.token)

    # Delete related contacts of this character
    contract_ids = []
    for character in Character.select().where(Character.character_id != this_character.character_id):
        contract_ids.append(character.character_id)

    this_char_authed_preston.get_op(
        "delete_characters_character_id_contacts",
        character_id=this_character.character_id,
        contact_ids=" ".join(contract_ids)
    )

    # Delete this contact for other characters
    for character in Character.select().where(Character.character_id != this_character.character_id):
        authed_preston = with_refresh(base_preston, character.token)
        authed_preston.get_op(
            "delete_characters_character_id_contacts",
            character_id=character.character_id,
            contact_ids=this_character.character_id
        )


@bot.event
async def on_ready():
    callback_server.start(base_preston, add_contacts)


@bot.command()
@command_error_handler
async def info(ctx):
    """Returns a list of currently registered Users and Characters"""
    users = User.select()
    if not users.exists():
        await ctx.send("No users registered.")
        return

    user_responses = []
    for user in users:

        character_names = []
        for character in user.characters:
            authed_preston = with_refresh(base_preston, character.token)
            character_name = authed_preston.whoami()["CharacterName"]
            character_names.append(f" - {character_name}")

        if character_names:
            character_names_body = "\n".join(character_names)
        else:
            character_names_body = "<no authorized characters>"
        user_responses = f"Registered Characters:\n{character_names_body}"

    if user_responses:
        user_responses_body = "\n".join(user_responses)
    else:
        user_responses_body = "<no authorized users>"
    response = f"Registered Users and Characters:\n{user_responses_body}"

    await send_large_message(ctx, response)


@bot.command()
@command_error_handler
async def characters(ctx):
    """Displays your currently authorized characters."""

    character_names = []
    user = User.get_or_none(User.user_id == str(ctx.author.id))
    if user is None:
        await ctx.send("You are not a registered user.")
        return

    for character in user.characters:
        char_auth = with_refresh(base_preston, character.token)
        character_name = char_auth.whoami()['CharacterName']
        character_names.append(f"- {character_name}")

    if character_names:
        character_names_body = "\n".join(character_names)
    else:
        character_names_body = "<no authorized characters>"
    response = f"You have the following character(s) authenticated:\n{character_names_body}"

    await send_large_message(ctx, response)


@bot.command()
@command_error_handler
async def invite(ctx, member: discord.Member):
    """Adds a user to be able to register characters"""
    if not ctx.author.id == int(os.getenv("ADMIN")):
        await ctx.send(f"You do not have rights to invite users.")
        return

    user, created = User.get_or_create(user_id=str(member.id))

    if created:
        await ctx.send(f"Invited {member}.")
    else:
        await ctx.send(f"{member} was already invited")


@bot.command()
@command_error_handler
async def kick(ctx, member: discord.Member):
    """Removes a user and their characters from contacts."""
    user = User.get_or_none(User.user_id == str(member.id))

    if user is None:
        await ctx.send("User not found.")
        return

    # Remove contacts from all other characters
    removed_character_names = []
    for character in user.characters:
        remove_contacts(character)
        char_auth = with_refresh(base_preston, character.token)
        character_name = char_auth.whoami()['CharacterName']
        character.delete()
        removed_character_names.append(character_name)

    # Delete User
    user.delete_instance()

    # Send output of what was removes
    response = f"Removed the User {user} and his characters:\n"
    for character_name in removed_character_names:
        response += f"  - {character_name}\n"
    await send_large_message(ctx, response)


@bot.command()
@command_error_handler
async def auth(ctx):
    """Sends you an authorization link for a character.
    :args: -c: authorize for your corporation"""

    secret_state = secrets.token_urlsafe(60)

    user = User.get_or_none(user_id=str(ctx.author.id))
    if user is None:
        await ctx.send(
            f"You do not have access to this bot, contact @<{os.getenv('ADMIN')}> so he allows you to register characters."
        )
        return

    Challenge.delete().where(Challenge.user == user).execute()
    Challenge.create(user=user, state=secret_state)

    full_link = f"{base_preston.get_authorize_url()}&state={secret_state}"
    await ctx.author.send(f"Use this [authentication link]({full_link}) to authorize your characters.")


@bot.command()
@command_error_handler
async def revoke(ctx, *args):
    """Revokes ESI access from all your characters.
    :args: Character that you want to revoke access to."""

    user = User.get_or_none(User.user_id == str(ctx.author.id))

    if user is None:
        await ctx.send(f"You did not have any authorized characters in the first place.")
        return

    if len(args) == 0:
        user_characters = Character.select().where(Character.user == user)
        if user_characters:
            for character in user_characters:
                remove_contacts(character)
                character.delete_instance()

        user.delete_instance()
        await ctx.send(f"Successfully revoked access to all your characters.")
        return

    else:
        try:
            character_id = await lookup(base_preston, " ".join(args), return_type="characters")
        except ValueError:
            args_concatenated = " ".join(args)
            await ctx.send(f"Args `{args_concatenated}` could not be parsed or looked up.")
            return
        character = user.characters.select().where(Character.character_id == character_id).first()
        if not character:
            await ctx.send("You have no character with that name linked.")
            return

        remove_contacts(character)
        character.delete_instance()
        await ctx.send(f"Successfully removed " + " ".join(args) + ".")


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])

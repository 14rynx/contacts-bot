import functools
import logging
import os
import secrets

import discord
from discord.ext import commands
from preston import Preston

from callback_server import callback_server
from models import initialize_database, User, Challenge, Character, ExternalContact
from utils import lookup, send_large_message

# Configure the logger
logger = logging.getLogger('discord.main')
logger.setLevel(logging.INFO)

# Standing to set for Contacts created by this bot
BOT_STANDING = float(os.getenv("STANDING", 5.5))
if BOT_STANDING in [-10, -5, 0, 5, 10]:
    logger.error("The standing value must not be a value able to set by players.")
    exit(1)

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


def with_refresh(preston_instance: Preston, refresh_token: str):
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


def add_character_contacts(preston: Preston, character_id: str, contacts_to_add: set[str]):
    """Add contracts for a character while not overwriting existing contracts"""

    contacts = preston.get_op(
        "get_characters_character_id_contacts",
        character_id=str(character_id)
    )

    existing_contracts = {str(c['contact_id']) for c in contacts if c.get('standing') > BOT_STANDING}
    contacts_to_add -= existing_contracts

    if len(contacts_to_add) == 0:
        return

    preston.post_op(
        'post_characters_character_id_contacts',
        path_data={
            "character_id": character_id,
            "standing": BOT_STANDING,
            "watched": False,
        },
        post_data=list(contacts_to_add),
    )


def delete_character_contacts(preston: Preston, character_id: str, contacts_to_delete: set[str]):
    """Delete contacts for a character while keeping contracts not by the bot"""
    contacts = preston.get_op(
        "get_characters_character_id_contacts",
        character_id=str(character_id)
    )

    contacts_with_wrong_standing = set(
        str(c['contact_id']) for c in contacts if
        c.get('standing') < BOT_STANDING - 1e-3 or c.get('standing') > BOT_STANDING + 1e-3
    )
    contacts_to_delete -= contacts_with_wrong_standing

    if len(contacts_to_delete) == 0:
        return

    preston.delete_op(
        "delete_characters_character_id_contacts",
        path_data={
            "character_id": str(character_id),
            "contact_ids": list(contacts_to_delete),
        },
    )


def remove_contact(this_character: Character):
    """Add all required contacts for a new linked character."""
    this_char_authed_preston = with_refresh(base_preston, this_character.token)

    contract_ids = set()

    # Delete this contact for other characters
    for character in Character.select().where(Character.character_id != this_character.character_id):
        authed_preston = with_refresh(base_preston, character.token)
        delete_character_contacts(authed_preston, character.character_id, {this_character.character_id})
        contract_ids.add(character.character_id)

    # Delete related contacts of this character
    delete_character_contacts(this_char_authed_preston, this_character.character_id, contract_ids)

    # Delete external contacts of this character
    external_contract_ids = set()
    for external_contact in ExternalContact.select():
        external_contract_ids.add(external_contact.contact_id)
    delete_character_contacts(this_char_authed_preston, this_character.character_id, external_contract_ids)


def add_contact(this_character: Character):
    """Remove all required contacts for a linked character"""

    # Got through registered characters and add this contact
    character_ids = set()
    for character in Character.select().where(Character.character_id != this_character.character_id):
        authed_preston = with_refresh(base_preston, character.token)
        add_character_contacts(authed_preston, character.character_id, {this_character.character_id})
        character_ids.add(character.character_id)

    # Add contacts to this character
    this_char_authed_preston = with_refresh(base_preston, this_character.token)
    add_character_contacts(this_char_authed_preston, this_character.character_id, character_ids)

    # Add external contacts to this
    external_contract_ids = set()
    for external_contact in ExternalContact.select():
        external_contract_ids.add(external_contact.contact_id)
    add_character_contacts(this_char_authed_preston, this_character.character_id, external_contract_ids)


def add_external_contact(contact_id: str):
    """Add external contact to all characters"""
    for character in Character.select():
        authed_preston = with_refresh(base_preston, character.token)
        add_character_contacts(authed_preston, character.character_id, {contact_id})


def delete_external_contact(contact_id: str):
    """Add external contact to all characters"""
    for character in Character.select():
        authed_preston = with_refresh(base_preston, character.token)
        delete_character_contacts(authed_preston, character.character_id, {contact_id})


@bot.event
async def on_ready():
    callback_server.start(base_preston, add_contact)


@bot.command()
@command_error_handler
async def info(ctx):
    """Returns a list of currently registered users and characters."""
    if not ctx.author.id == int(os.getenv("ADMIN")):
        await ctx.send(f"You do not have rights to display all info.")
        return

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
            character_names_body = "\n<no authorized characters>"
        user_responses.append(f"### User <@{user.user_id}>\n{character_names_body}")

    if user_responses:
        user_responses_body = "\n".join(user_responses)
    else:
        user_responses_body = "<no authorized users>"
    response = f"## Users\n{user_responses_body}"

    await send_large_message(ctx, response, allowed_mentions=discord.AllowedMentions(users=False))


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
    response = f"## Characters\n{character_names_body}"

    await send_large_message(ctx, response)


@bot.command()
@command_error_handler
async def invite(ctx, member: discord.Member):
    """Adds a user to be able to register characters."""
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
    if not ctx.author.id == int(os.getenv("ADMIN")):
        await ctx.send(f"You do not have rights to kick users.")
        return

    await ctx.send(f"Kicking {member} ...")

    user = User.get_or_none(User.user_id == str(member.id))

    if user is None:
        await ctx.send("User not found.")
        return

    # Remove contacts from all other characters
    removed_character_names = []
    for character in user.characters:
        remove_contact(character)
        char_auth = with_refresh(base_preston, character.token)
        character_name = char_auth.whoami()['CharacterName']
        character.delete_instance()
        removed_character_names.append(character_name)

    # Delete User
    user.delete_instance()

    # Send output of what was removes
    response = f"Removed the user <@{user.user_id}> and his characters:\n"
    for character_name in removed_character_names:
        response += f" - {character_name}\n"
    await send_large_message(ctx, response)


@bot.command()
@command_error_handler
async def auth(ctx):
    """Sends you an authorization link for characters."""

    secret_state = secrets.token_urlsafe(60)

    user = User.get_or_none(user_id=str(ctx.author.id))
    if user is None:
        await ctx.send(
            f"You do not have access to this bot, contact <@{os.getenv('ADMIN')}> so he allows you to register characters."
        )
        return

    Challenge.delete().where(Challenge.user == user).execute()
    Challenge.create(user=user, state=secret_state)

    full_link = f"{base_preston.get_authorize_url()}&state={secret_state}"
    await ctx.author.send(f"Use this [authentication link]({full_link}) to authorize your characters.")


@bot.command()
@command_error_handler
async def revoke(ctx, *args):
    """Revokes ESI access for your characters.
    :args: Character that you want to revoke access to.
    If no arguments are provided, revokes all characters."""

    user = User.get_or_none(User.user_id == str(ctx.author.id))

    if user is None:
        await ctx.send(f"You did not have any authorized characters in the first place.")
        return

    if len(args) == 0:
        await ctx.send(f"Revoking all your characters ...")

        user_characters = Character.select().where(Character.user == user)
        if user_characters:
            for character in user_characters:
                remove_contact(character)
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

        remove_contact(character)
        character.delete_instance()
        await ctx.send(f"Successfully removed " + " ".join(args) + ".")


@bot.command()
@command_error_handler
async def add_external(ctx, *args):
    """Add an external (unauthenticated) character / corporation / alliance to all characters
    Use -c or --corporation for corporations and -a or --alliance for alliances."""

    if not ctx.author.id == int(os.getenv("ADMIN")):
        await ctx.send(f"You do not have rights to add external contacts.")
        return

    if len(args) == 0:
        await ctx.send("Please provide a character / corporation / alliance.")
        return

    if args[0] in ["-c", "--corporation"]:
        return_type = "corporations"
        data = " ".join(args[1:])
    elif args[0] in ["-a", "--alliance"]:
        return_type = "alliances"
        data = " ".join(args[1:])
    else:
        return_type = "characters"
        data = " ".join(args)

    try:
        contact_id = await lookup(base_preston, data, return_type=return_type)
    except ValueError:
        args_concatenated = " ".join(args)
        await ctx.send(f"Args `{args_concatenated}` could not be parsed or looked up.")
        return

    contact, created = ExternalContact.get_or_create(
        contact_id=contact_id,
    )


    add_external_contact(contact_id)

    if created:
        await ctx.send(f"Successfully added {data} as a contact." )
    else:
        await ctx.send(f"Successfully re-added {data} as a contact." )

@bot.command()
@command_error_handler
async def remove_external(ctx, *args):
    """Remove an external (unauthenticated) character / corporation / alliance to all characters
    Use -c or --corporation for corporations and -a or --alliance for alliances."""
    if not ctx.author.id == int(os.getenv("ADMIN")):
        await ctx.send(f"You do not have rights to remove external contacts.")
        return

    if len(args) == 0:
        await ctx.send("Please provide a character / corporation / alliance.")
        return

    if args[0] in ["-c", "--corporation"]:
        return_type = "corporations"
        data = " ".join(args[1:])
    elif args[0] in ["-a", "--alliance"]:
        return_type = "alliances"
        data = " ".join(args[1:])
    else:
        return_type = "characters"
        data = " ".join(args)

    try:
        contact_id = await lookup(base_preston, data, return_type=return_type)
    except ValueError:
        args_concatenated = " ".join(args)
        await ctx.send(f"Args `{args_concatenated}` could not be parsed or looked up.")
        return

    contact = ExternalContact.get_or_none(
        contact_id=contact_id,
    )

    if contact is None:
        await ctx.send(f"{data} was not added and thus can not be removed.")
        return

    delete_external_contact(contact_id)

    contact.delete_instance()
    await ctx.send(f"Successfully removed {data}.")


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])

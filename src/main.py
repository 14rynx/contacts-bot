import functools
import logging
import os
import secrets
from typing import Literal

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from preston import Preston
from requests.exceptions import HTTPError

from callback_server import callback_server
from models import initialize_database, User, Challenge, Character, ExternalContact
from utils import lookup

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
        interaction, *arguments = args
        logger.info(f"{interaction.user.name} used !{func.__name__} {arguments} {kwargs}")

        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in !{func.__name__} command: {e}", exc_info=True)

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
    try:
        this_char_authed_preston = with_refresh(base_preston, this_character.token)
    except HTTPError as exp:
        if exp.response.status_code == 401:
            return
        else:
            raise

    contract_ids = set()

    # Delete this contact for other characters
    for character in Character.select().where(Character.character_id != this_character.character_id):
        try:
            authed_preston = with_refresh(base_preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                continue
            else:
                raise
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
        try:
            authed_preston = with_refresh(base_preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                continue
            else:
                raise
        add_character_contacts(authed_preston, character.character_id, {this_character.character_id})
        character_ids.add(character.character_id)

    # Add contacts to this character
    try:
        this_char_authed_preston = with_refresh(base_preston, this_character.token)
    except HTTPError as exp:
        if exp.response.status_code == 401:
            return
        else:
            raise
    add_character_contacts(this_char_authed_preston, this_character.character_id, character_ids)

    # Add external contacts to this
    external_contract_ids = set()
    for external_contact in ExternalContact.select():
        external_contract_ids.add(external_contact.contact_id)
    add_character_contacts(this_char_authed_preston, this_character.character_id, external_contract_ids)


def add_external_contact(contact_id: str):
    """Add external contact to all characters"""
    for character in Character.select():
        try:
            authed_preston = with_refresh(base_preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                continue
            else:
                raise
        add_character_contacts(authed_preston, character.character_id, {contact_id})


def delete_external_contact(contact_id: str):
    """Add external contact to all characters"""
    for character in Character.select():
        try:
            authed_preston = with_refresh(base_preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                continue
            else:
                raise
        delete_character_contacts(authed_preston, character.character_id, {contact_id})


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}", exc_info=True)
    callback_server.start(base_preston, add_contact)


@bot.tree.command(name="info", description="Returns a list of currently registered users and characters.")
@command_error_handler
async def info(interaction: discord.Interaction):
    """Returns a list of currently registered users and characters."""
    if interaction.user.id != int(os.getenv("ADMIN")):
        await interaction.response.send_message("You do not have rights to display all info.", ephemeral=True)
        return
    else:
        await interaction.response.send_message("Fetching all characters...", ephemeral=True)

    user_responses = []
    dead_characters = []
    users = User.select()
    if not users.exists():
        user_responses.append(f"<no users registered>")
    else:
        for user in users:
            character_names = []
            for character in user.characters:
                try:
                    authed_preston = with_refresh(base_preston, character.token)
                except HTTPError as exp:
                    character_name = base_preston.get_op(
                        "get_characters_character_id",
                        character_id=character.character_id
                    ).get("name")
                    dead_characters.append(f" - {character_name}")
                    continue
                character_name = authed_preston.whoami()["CharacterName"]
                character_names.append(f" - {character_name}")

            if character_names:
                character_names_body = "\n".join(character_names)
            else:
                character_names_body = "\n<no authorized characters>"
            user_responses.append(f"### User <@{user.user_id}>\n{character_names_body}")

    if user_responses:
        user_responses_body = "## Users\n"
        user_responses_body += "\n".join(user_responses)
    else:
        user_responses_body = "<no authorized users>"

    if dead_characters:
        dead_character_response_body = "\n## Characters with broken permissions\n"
        dead_character_response_body += "\n".join(dead_characters)
    else:
        dead_character_response_body = ""

    # Deal with externally linked Characters, Corporations or Alliances
    external_response = []
    externals = ExternalContact.select()
    if not externals.exists():
        external_response.append(f"<no users registered>")
    else:

        results = base_preston.post_op(
            "post_universe_names",
            path_data={"datasource": "tranquility"},  # Added because Preston is broken
            post_data=[e.contact_id for e in externals],
        )

        for external_type in ["character", "corporation", "alliance"]:
            externals_per_type = []
            for result in results:
                if result.get("category") == external_type:
                    externals_per_type.append(f" - {result.get('name')}")

            if externals_per_type:
                external_type_body = "\n".join(externals_per_type)
            else:
                external_type_body = f"<no authorized {external_type}s>"

            external_response.append(f"### External {external_type.capitalize()}s\n{external_type_body}")

    if external_response:
        external_response_body = "\n## Externals\n"
        external_response_body += "\n".join(external_response)
    else:
        external_response_body = ""

    response = f"{user_responses_body}{dead_character_response_body}{external_response_body}"

    await interaction.followup.send(response, ephemeral=True)


@bot.tree.command(name="characters", description="Displays your currently authorized characters..")
@command_error_handler
async def characters(interaction: discord.Interaction):
    character_names = []
    dead_characters = []

    user = User.get_or_none(User.user_id == str(interaction.user.id))
    if user is None:
        await interaction.response.send_message("You are not a registered user.")
        return

    await interaction.response.send_message("Fetching all characters...", ephemeral=True)

    for character in user.characters:
        try:
            char_auth = with_refresh(base_preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                dead_characters.append(character.character_id)
                continue
            else:
                raise
        character_name = char_auth.whoami()['CharacterName']
        character_names.append(f"- {character_name}")

    if character_names:
        character_names_body = "## Characters"
        character_names_body += "\n".join(character_names)
    else:
        character_names_body = "<no authorized characters>"

    if dead_characters:
        dead_character_response_body = "\n## Characters with broken permissions"
        dead_character_response_body += "\n".join(dead_characters)
    else:
        dead_character_response_body = ""

    response = f"{character_names_body}{dead_character_response_body}"

    await interaction.followup.send(response, ephemeral=True)


@bot.tree.command(name="invite", description="Adds a user to be able to register characters.")
async def invite(interaction: Interaction, member: discord.Member):
    """Slash command to invite a user to register characters."""
    if interaction.user.id != int(os.getenv("ADMIN")):
        await interaction.response.send_message("You do not have rights to invite users.")
        return

    user, created = User.get_or_create(user_id=str(member.id))

    if created:
        await interaction.response.send_message(f"Invited {member.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{member.mention} was already invited.", ephemeral=True)


@bot.tree.command(name="kick", description="Removes a user and their characters from contacts.")
@command_error_handler
async def kick(interaction: Interaction, member: discord.Member):
    """Slash command to remove a user and their characters from the system."""
    if interaction.user.id != int(os.getenv("ADMIN")):
        await interaction.response.send_message("You do not have rights to kick users.")
        return

    await interaction.response.send_message(f"Kicking {member.mention} ...", ephemeral=True)

    user = User.get_or_none(User.user_id == str(member.id))
    if user is None:
        await interaction.followup.send("User not found.", ephemeral=True)
        return

    removed_character_names = []
    for character in user.characters:
        remove_contact(character)
        try:
            char_auth = with_refresh(base_preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                await interaction.followup.send("ESI permissions broken.", ephemeral=True)
                return
            else:
                raise
        character_name = char_auth.whoami()['CharacterName']
        character.delete_instance()
        removed_character_names.append(character_name)

    user.delete_instance()

    response = f"Removed the user <@{member.id}> and their characters:\n"
    for character_name in removed_character_names:
        response += f" - {character_name}\n"

    await interaction.followup.send(response, ephemeral=True)


@bot.tree.command(name="auth", description="Sends you an authorization link for characters.")
@command_error_handler
async def auth(interaction: Interaction):
    secret_state = secrets.token_urlsafe(60)

    user = User.get_or_none(user_id=str(interaction.user.id))
    if user is None:
        await interaction.response.send_message(
            f"You do not have access to this bot, contact <@{os.getenv('ADMIN')}> so he allows you to register characters."
        )
        return

    Challenge.delete().where(Challenge.user == user).execute()
    Challenge.create(user=user, state=secret_state)

    full_link = f"{base_preston.get_authorize_url()}&state={secret_state}"
    await interaction.response.send_message(
        f"Use this [authentication link]({full_link}) to authorize your characters.", ephemeral=True)


@bot.tree.command(
    name="revoke",
    description="Revokes ESI access for your characters."
)
@app_commands.describe(
    character_name="Name of the character to revoke, revoke all if empty."
)
@command_error_handler
async def revoke(interaction: Interaction, character_name: str | None = None):
    """Revokes ESI access for your characters.
    :args: Character that you want to revoke access to.
    If no arguments are provided, revokes all characters."""

    user = User.get_or_none(User.user_id == str(interaction.user.id))

    if user is None:
        await interaction.response.send_message(f"You did not have any authorized characters in the first place.")
        return

    if character_name is None:
        await interaction.response.send_message(f"Revoking all your characters ...", ephemeral=True)

        user_characters = Character.select().where(Character.user == user)
        if user_characters:
            for character in user_characters:
                remove_contact(character)
                character.delete_instance()

        user.delete_instance()
        await interaction.followup.send(f"Successfully revoked access to all your characters.", ephemeral=True)
        return

    else:
        try:
            character_id = await lookup(base_preston, character_name, return_type="characters")
        except ValueError:
            await interaction.response.send_message(f"Args `{character_name}` could not be parsed or looked up.")
            return
        character = user.characters.select().where(Character.character_id == character_id).first()
        if not character:
            await interaction.response.send_message("You have no character with that name linked.")
            return

        remove_contact(character)
        character.delete_instance()
        await interaction.response.send_message(f"Successfully removed {character_name}.", ephemeral=True)


@bot.tree.command(
    name="add_external",
    description="Add an external character, corporation, or alliance to contacts."
)
@app_commands.describe(
    entity_type="Type of entity to add.",
    entity_name="Name of the character, corporation, or alliance to add."
)
@command_error_handler
async def add_external(
        interaction: Interaction,
        entity_type: Literal["character", "corporation", "alliance"],
        entity_name: str
):
    if not interaction.user.id == int(os.getenv("ADMIN")):
        await interaction.response.send_message(f"You do not have rights to add external contacts.")
        return

    try:
        contact_id = await lookup(base_preston, entity_name, return_type=entity_type + "s")
    except ValueError:
        await interaction.response.send_message(f"Args `{entity_name}` could not be parsed or looked up.")
        return

    contact, created = ExternalContact.get_or_create(
        contact_id=contact_id,
    )

    add_external_contact(contact_id)

    if created:
        await interaction.response.send_message(f"Successfully added {entity_name} as a contact.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Successfully re-added {entity_name} as a contact.", ephemeral=True)


@bot.tree.command(
    name="remove_external",
    description="Remove an external (unauthenticated) character / corporation / alliance from contacts."
)
@app_commands.describe(
    entity_type="Type of entity to remove.",
    entity_name="Name of the character, corporation, or alliance to remove."
)
@command_error_handler
async def remove_external(
        interaction: Interaction,
        entity_type: Literal["character", "corporation", "alliance"],
        entity_name: str
):
    """"""
    if not interaction.user.id == int(os.getenv("ADMIN")):
        await interaction.response.send_message(f"You do not have rights to add external contacts.")
        return

    try:
        contact_id = await lookup(base_preston, entity_name, return_type=entity_type + "s")
    except ValueError:
        await interaction.response.send_message(f"Args `{entity_name}` could not be parsed or looked up.")
        return

    contact = ExternalContact.get_or_none(
        contact_id=contact_id,
    )

    if contact is None:
        await interaction.response.send_message(
            f"{entity_name} was not added and thus can not be removed.", ephemeral=True
        )
        return

    delete_external_contact(contact_id)

    contact.delete_instance()
    await interaction.response.send_message(f"Successfully removed {entity_name}.", ephemeral=Tru)


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])

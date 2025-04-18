import logging
import os

from preston import Preston
from requests.exceptions import HTTPError

from models import Character, ExternalContact
from utils import with_refresh

logger = logging.getLogger("discord.main.contacts")

# Standing to set for Contacts created by this bot
BOT_STANDING = float(os.getenv("STANDING", 5.5))
if BOT_STANDING in [-10, -5, 0, 5, 10]:
    logger.error("The standing value must not be a value able to set by players.")
    exit(1)


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


def remove_contact(this_character: Character, preston: Preston):
    """Add all required contacts for a new linked character."""
    try:
        this_char_authed_preston = with_refresh(preston, this_character.token)
    except HTTPError as exp:
        if exp.response.status_code == 401:
            return
        else:
            raise

    contract_ids = set()

    # Delete this contact for other characters
    for character in Character.select().where(Character.character_id != this_character.character_id):
        try:
            authed_preston = with_refresh(preston, character.token)
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


def add_contact(this_character: Character, preston: Preston):
    """Remove all required contacts for a linked character"""

    # Got through registered characters and add this contact
    character_ids = set()
    for character in Character.select().where(Character.character_id != this_character.character_id):
        try:
            authed_preston = with_refresh(preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                continue
            else:
                raise
        add_character_contacts(authed_preston, character.character_id, {this_character.character_id})
        character_ids.add(character.character_id)

    # Add contacts to this character
    try:
        this_char_authed_preston = with_refresh(preston, this_character.token)
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


def add_external_contact(contact_id: str, preston: Preston):
    """Add external contact to all characters"""
    for character in Character.select():
        try:
            authed_preston = with_refresh(preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                continue
            else:
                raise
        add_character_contacts(authed_preston, character.character_id, {contact_id})


def remove_external_contact(contact_id: str, preston: Preston):
    """Add external contact to all characters"""
    for character in Character.select():
        try:
            authed_preston = with_refresh(preston, character.token)
        except HTTPError as exp:
            if exp.response.status_code == 401:
                continue
            else:
                raise
        delete_character_contacts(authed_preston, character.character_id, {contact_id})

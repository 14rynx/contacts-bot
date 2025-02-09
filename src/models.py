from peewee import *

# Initialize the database
db = SqliteDatabase('data/bot.db')


class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    user_id = CharField(primary_key=True)


class Character(BaseModel):
    character_id = CharField(primary_key=True)
    user = ForeignKeyField(User, backref='characters')
    token = TextField()


class ExternalContact(BaseModel):
    contact_id = CharField(primary_key=True)


class Challenge(BaseModel):
    user = ForeignKeyField(User, backref='challenges')
    state = CharField()


def initialize_database():
    with db:
        db.create_tables([User, Character, ExternalContact, Challenge])

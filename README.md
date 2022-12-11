# opsdroid-connector-signald
Opsdroid connector for Signal Messenger using [signald](https://signald.org)

## configuration

```yml
connectors:
  signald:
    # URL of this repository for Opsdroid to automatically download
    # the plugin from.
    repo: https://github.com/awahlig/opsdroid-connector-signald.git

    # Path to the unix socket used to communicate with signald.
    socket-path: /signald/signald.sock

    # Phone number that signald has been registered with / linked to.
    bot-number: "+1234567890"

    # Directory used to store outgoing attachments before the paths to
    # them are handed over to signald. Note that when using docker, this
    # directory needs to be added to the signald container as well.
    outgoing-path: /attachments

    # Optional aliases for Signal phone numbers and group IDs.
    # Makes working with some skills easier.
    rooms:
      "john": "+2134567890"
      "family": "group.RVZ5..."

    # Optional list of Signal phone numbers that can talk to the bot.
    # If not empty, numbers that are not on the list are ignored.
    whitelisted-numbers:
      - "+3214567890"
      - "alias"
```

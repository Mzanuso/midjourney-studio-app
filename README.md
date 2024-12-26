# Midjourney Studio App

A PyQt5-based desktop application for managing Midjourney images and prompts.

## Features

- Image gallery management
- Discord integration for Midjourney
- Claude AI integration for image analysis
- Advanced prompt generation
- Image categorization and tagging
- Automatic metadata management

## Installation

```bash
# Clone the repository
git clone https://github.com/Mzanuso/midjourney-studio-app.git

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Create a `config.json` in the APP directory with:

```json
{
    "USER_TOKEN": "your-discord-token",
    "GUILD_ID": "your-guild-id",
    "CHANNEL_ID": "your-channel-id"
}
```

## Usage

Run the main application:

```bash
python src/main.py
```
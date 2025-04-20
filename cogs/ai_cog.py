import discord
from discord import app_commands
from discord.ext import commands
import google.generativeai as genai
import os
import logging
from dotenv import load_dotenv

load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Configure Google AI Client
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        logging.info("Google AI Client configured successfully (AICog).")
    except Exception as e:
         logging.error(f"Failed to configure Google AI Client (AICog): {e}")
         GOOGLE_API_KEY = None
else:
    logging.warning("GOOGLE_API_KEY not found. AICog will be limited.")

safety_settings = [ # Example safety settings
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    # ... Add others as needed
]
generation_config = {"max_output_tokens": 2000}

class AICog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.model = None
        if GOOGLE_API_KEY:
            try:
                self.model = genai.GenerativeModel(
                    model_name="gemini-1.5-flash", # Or your preferred model
                    generation_config=generation_config,
                    safety_settings=safety_settings
                )
                logging.info("Google AI Model initialized (AICog).")
            except Exception as e:
                logging.error(f"Failed to initialize Google AI Model (AICog): {e}")
        else:
             logging.warning("Google AI Model not initialized in AICog (No API Key).")

    @app_commands.command(name="ask", description="Ask the AI (Gemini) a question.")
    @app_commands.describe(prompt="The question or prompt for the AI.")
    async def ask_command(self, interaction: discord.Interaction, prompt: str):
        if not self.model:
            await interaction.response.send_message("AI module not available.", ephemeral=True); return

        await interaction.response.defer(thinking=True)
        try:
            response = await self.model.generate_content_async(prompt)
            ai_response_text = ""
            if response.parts: ai_response_text = response.text
            elif response.prompt_feedback and response.prompt_feedback.block_reason:
                 ai_response_text = f"⚠️ Response blocked: {response.prompt_feedback.block_reason.name}"
                 logging.warning(f"AI blocked: {response.prompt_feedback.block_reason.name}. Prompt: '{prompt}'")
            else: ai_response_text = "😕 Empty response from AI."; logging.warning(f"AI empty response. Prompt: '{prompt}'")

            if len(ai_response_text) > 1950: ai_response_text = ai_response_text[:1950] + "... (truncated)" # Adjust limit slightly

            await interaction.followup.send(f">>> {interaction.user.mention} asked:\n> {prompt}\n\n**AI:**\n{ai_response_text}")

        except Exception as e:
            logging.error(f"Error during AI generation: {e}")
            await interaction.followup.send(f"❌ AI Error: {e}")


async def setup(bot: commands.Bot):
    # Only add cog if API key was valid during initial check
    if GOOGLE_API_KEY:
        await bot.add_cog(AICog(bot))
        logging.info("AICog loaded.")
    else:
         logging.error("Skipping AICog load: GOOGLE_API_KEY missing or invalid.")

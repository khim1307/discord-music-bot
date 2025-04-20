import discord
from discord import app_commands
from discord.ext import commands
import google.generativeai as genai
import os
import logging
from dotenv import load_dotenv

# Load environment variables specifically for the Cog if needed elsewhere,
# but main bot loads .env anyway. Ensure key is loaded here for setup.
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Configure the Google AI client
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        logging.info("Google AI Client configured successfully.")
    except Exception as e:
         logging.error(f"Failed to configure Google AI Client: {e}")
         # Optionally raise an error or handle inability to configure
         # raise ImportError("Google AI Client configuration failed. Check API Key.") from e
         GOOGLE_API_KEY = None # Indicate failure
else:
    logging.warning("GOOGLE_API_KEY not found in environment variables. AI Cog will be limited.")
    # Set key to None to prevent errors later if configuration failed

# --- Define Safety Settings (Optional - Adjust as needed) ---
# You can customize these to block more/less content.
# Refer to Google AI documentation for details.
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

# --- Define Generation Config (Optional - Adjust as needed) ---
generation_config = {
    # "temperature": 0.9, # Controls randomness (0=deterministic, >1=more creative)
    # "top_p": 1, # Nucleus sampling parameter
    # "top_k": 1, # Top-k sampling parameter
    "max_output_tokens": 2000, # Limit response length (Discord limit is 2000)
}

class AICog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.model = None
        if GOOGLE_API_KEY: # Only initialize model if key/config is valid
            try:
                # Choose your model - 'gemini-1.5-flash' is fast and capable
                self.model = genai.GenerativeModel(
                    model_name="gemini-1.5-flash",
                    generation_config=generation_config,
                    safety_settings=safety_settings
                )
                logging.info("Google AI Model 'gemini-1.5-flash' initialized.")
            except Exception as e:
                logging.error(f"Failed to initialize Google AI Model: {e}")
        else:
             logging.warning("Google AI Model not initialized due to missing/invalid API Key.")

    @app_commands.command(name="ask", description="Ask the AI (Gemini) a question.")
    @app_commands.describe(prompt="The question or prompt for the AI.")
    async def ask_command(self, interaction: discord.Interaction, prompt: str):
        """Handler for the /ask slash command."""

        if not self.model:
            await interaction.response.send_message("Sorry, the AI module is not configured or failed to initialize. Please check the bot logs.", ephemeral=True)
            return

        # Defer response as AI generation can take time
        await interaction.response.defer(ephemeral=False, thinking=True) # Show "Bot is thinking..." publicly

        try:
            # Generate content
            # For simple Q&A, just send the prompt.
            # For conversation, you'd build a history list: [{'role':'user', 'parts': [prompt]}]
            response = await self.model.generate_content_async(prompt) # Use async version

            ai_response_text = ""
            # Check for safety blocks before accessing text
            if response.parts:
                ai_response_text = response.text
            elif response.prompt_feedback and response.prompt_feedback.block_reason:
                 ai_response_text = f"⚠️ Response blocked due to: {response.prompt_feedback.block_reason.name}"
                 logging.warning(f"AI response blocked. Reason: {response.prompt_feedback.block_reason.name}. Prompt: '{prompt}'")
            else:
                 # This case might happen if the response is empty for other reasons
                 ai_response_text = "😕 The AI returned an empty response."
                 logging.warning(f"AI returned empty response. Prompt: '{prompt}'. Full Response: {response}")


            # Handle Discord's message length limit (2000 chars)
            if len(ai_response_text) > 2000:
                logging.warning(f"AI response truncated for length ({len(ai_response_text)} > 2000)")
                ai_response_text = ai_response_text[:1990] + "... (truncated)"

            # Send the AI's response as a followup
            await interaction.followup.send(f">>> {interaction.user.mention} asked:\n> {prompt}\n\n**AI Response:**\n{ai_response_text}")

        except Exception as e:
            logging.error(f"Error during AI generation or sending response: {e}")
            # Send error message as followup
            try:
                await interaction.followup.send(f"❌ Sorry, an error occurred while processing your request: {e}")
            except Exception as followup_e: # Handle case where followup sending also fails
                 logging.error(f"Failed to send error followup: {followup_e}")


# Setup function to add the Cog to the bot
async def setup(bot: commands.Bot):
    if not GOOGLE_API_KEY:
        logging.error("Cannot load AICog: GOOGLE_API_KEY is missing or invalid.")
        return # Prevent loading if key is bad
    await bot.add_cog(AICog(bot))
    logging.info("AICog loaded successfully.")

import logging
from typing import Optional

import discord
from discord import app_commands

from services.consent_service import ConsentService, ConsentLevel
from services.backfill_service import BackfillService

logger = logging.getLogger(__name__)


def register_consent_commands(
    tree: app_commands.CommandTree,
    consent_service: ConsentService,
    backfill_service: Optional[BackfillService] = None,
):
    """Register all consent-related commands to the command tree"""

    @app_commands.command(
        name="consent", description="Give consent for the Jade bot to log your messages"
    )
    async def give_consent(interaction: discord.Interaction):
        """Show consent information and modal to user"""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server!", ephemeral=True
            )
            return

        # Show information about consent levels with a button to proceed
        embed = discord.Embed(
            title="🤖 Jade AI Data Collection Consent",
            description=(
                "Jade uses logged messages to improve its AI features. "
                "Your consent helps make Jade smarter!"
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="📊 Consent Levels",
            value=(
                "**Level 1 - Metadata Only**\n"
                "└ Log when you send messages (timestamp, channel)\n\n"
                "**Level 2 - Content** ⭐ Recommended\n"
                "└ Level 1 + your message text\n\n"
                "**Level 3 - Full**\n"
                "└ Level 2 + attachments (images, files)\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔒 Your Privacy Rights",
            value=(
                "• Data is used **only** to enhance Jade's AI\n"
                "• You can change or revoke consent anytime\n"
                "• Revoking deletes **all** your logged data\n"
                "• Bot messages are always logged (not affected by consent)"
            ),
            inline=False,
        )
        embed.set_footer(
            text="Select your consent level and click the button to proceed"
        )

        view = ConsentInfoView(consent_service, interaction.user, backfill_service)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="consent_status", description="Check your current consent status"
    )
    async def check_consent_status(interaction: discord.Interaction):
        """Check user's consent status"""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server!", ephemeral=True
            )
            return

        consent_record = await consent_service.get_user_consent(
            interaction.guild.id, interaction.user.id
        )

        if not consent_record or not consent_record.get("consent_active", False):
            await interaction.response.send_message(
                "ℹ️ You have **not given consent** for data collection.\n\n"
                "Your messages are **not being logged**.\n\n"
                "Use `/consent` to give consent if you'd like to help improve Jade's AI features.",
                ephemeral=True,
            )
            return

        consent_level = ConsentLevel(consent_record.get("consent_level", 0))
        consented_at = consent_record.get("consented_at")

        level_description = consent_service.get_consent_level_description(consent_level)

        embed = discord.Embed(
            title="✅ Your Consent Status",
            description="You have given consent for data collection.",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="📊 Consent Level",
            value=f"**{consent_level.name}** (Level {consent_level.value})",
            inline=False,
        )
        embed.add_field(
            name="📝 What's Being Logged", value=level_description, inline=False
        )
        if consented_at:
            embed.add_field(
                name="📅 Consented On",
                value=f"<t:{int(consented_at.timestamp())}:F>",
                inline=False,
            )
        embed.set_footer(
            text="Use /revoke_consent to withdraw your consent at any time"
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="revoke_consent",
        description="Revoke your consent and delete all your logged data",
    )
    async def revoke_consent(interaction: discord.Interaction):
        """Revoke user's consent and delete their data"""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server!", ephemeral=True
            )
            return

        # Check if user has given consent
        consent_record = await consent_service.get_user_consent(
            interaction.guild.id, interaction.user.id
        )

        if not consent_record or not consent_record.get("consent_active", False):
            await interaction.response.send_message(
                "ℹ️ You have not given consent, so there's nothing to revoke.",
                ephemeral=True,
            )
            return

        # Show confirmation view
        view = RevokeConsentView(
            consent_service, interaction.guild.id, interaction.user.id
        )

        await interaction.response.send_message(
            "⚠️ **Are you sure you want to revoke consent?**\n\n"
            "This will:\n"
            "• Stop logging your future messages\n"
            "• **Permanently delete** all your previously logged messages and attachments\n"
            "• Remove your consent record\n\n"
            "**This action cannot be undone!**\n\n"
            "Click the button below to confirm within 60 seconds.",
            view=view,
            ephemeral=True,
        )

    # Register commands
    tree.add_command(give_consent)
    tree.add_command(check_consent_status)
    tree.add_command(revoke_consent)


class ConsentModal(discord.ui.Modal, title="🤖 Jade Data Collection Consent"):
    """Modal for collecting user consent"""

    def __init__(
        self,
        consent_service: ConsentService,
        expected_username: str,
        consent_level: ConsentLevel,
        backfill_service: Optional[BackfillService] = None,
        backfill_historical: bool = False,
    ):
        super().__init__()
        self.consent_service = consent_service
        self.expected_username = expected_username
        self.consent_level = consent_level
        self.backfill_service = backfill_service
        self.backfill_historical = backfill_historical

    username_input = discord.ui.TextInput(
        label="Type your username to confirm",
        placeholder="Enter your exact Discord username",
        required=True,
        min_length=1,
        max_length=32,
        style=discord.TextStyle.short,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission"""
        try:
            # Validate username
            entered_username = self.username_input.value.strip()
            if entered_username != self.expected_username:
                await interaction.response.send_message(
                    f"❌ Username does not match! Expected: `{self.expected_username}`\n\n"
                    "Please enter your exact Discord username to confirm.",
                    ephemeral=True,
                )
                return

            # Get user's join date in this guild
            member = interaction.guild.get_member(interaction.user.id)
            joined_at = member.joined_at if member else None

            # Grant consent
            await self.consent_service.grant_consent(
                guild_id=interaction.guild.id,
                guild_name=interaction.guild.name,
                user_id=interaction.user.id,
                user_name=interaction.user.name,
                consent_level=self.consent_level,
                initials=entered_username,  # Store username instead of initials
                backfill_historical=self.backfill_historical,
                joined_at=joined_at,
            )

            # Send confirmation
            level_description = self.consent_service.get_consent_level_description(
                self.consent_level
            )

            embed = discord.Embed(
                title="✅ Consent Recorded",
                description="Thank you for helping improve Jade!",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="📊 Your Consent Level",
                value=f"**{self.consent_level.name}** (Level {self.consent_level.value})",
                inline=False,
            )
            embed.add_field(
                name="📝 What Will Be Logged", value=level_description, inline=False
            )
            embed.add_field(
                name="🔒 Your Privacy",
                value=(
                    "• This data is used solely to enhance Jade's AI features\n"
                    "• You can revoke consent at any time using `/revoke_consent`\n"
                    "• Revoking will permanently delete all your logged data"
                ),
                inline=False,
            )
            embed.set_footer(text=f"Confirmed by: {entered_username}")

            await interaction.response.send_message(embed=embed, ephemeral=True)

            # Trigger backfill if requested
            if self.backfill_historical and self.backfill_service and joined_at:
                await interaction.followup.send(
                    "🔄 Starting retroactive message collection... This may take a while depending on your message history.",
                    ephemeral=True,
                )

                try:
                    (
                        success,
                        failed,
                    ) = await self.backfill_service.backfill_user_messages(
                        guild=interaction.guild,
                        user_id=interaction.user.id,
                        after=joined_at,
                    )

                    await interaction.followup.send(
                        f"✅ Retroactive collection complete!\n\n"
                        f"**Messages collected:** {success:,}\n"
                        f"**Failed:** {failed:,}",
                        ephemeral=True,
                    )
                except Exception as e:
                    logger.error(
                        f"Error during retroactive backfill: {e}", exc_info=True
                    )
                    await interaction.followup.send(
                        f"⚠️ Retroactive collection encountered an error: {e}\n\n"
                        f"Your consent is still active and future messages will be logged.",
                        ephemeral=True,
                    )

        except Exception as e:
            logger.error(f"Error processing consent: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Error processing consent: {e}", ephemeral=True
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        """Handle errors in modal"""
        logger.error(f"Error in consent modal: {error}", exc_info=True)
        await interaction.response.send_message(
            "❌ An error occurred while processing your consent. Please try again.",
            ephemeral=True,
        )


class RevokeConsentView(discord.ui.View):
    """View with confirmation button for revoking consent"""

    def __init__(self, consent_service: ConsentService, guild_id: int, user_id: int):
        super().__init__(timeout=60.0)
        self.consent_service = consent_service
        self.guild_id = guild_id
        self.user_id = user_id

    @discord.ui.button(label="🗑️ Yes, Delete My Data", style=discord.ButtonStyle.danger)
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Confirm revocation"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Only the command user can confirm!", ephemeral=True
            )
            return

        # Disable buttons
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        # Revoke consent
        await self.consent_service.revoke_consent(self.guild_id, self.user_id)

        # Delete user data
        await interaction.followup.send(
            "🔄 Deleting your data... This may take a moment.", ephemeral=True
        )

        deletion_result = await self.consent_service.delete_user_data(
            self.guild_id, self.user_id
        )

        # Send confirmation
        embed = discord.Embed(
            title="✅ Consent Revoked",
            description="Your consent has been revoked and your data has been deleted.",
            color=discord.Color.red(),
        )
        embed.add_field(
            name="🗑️ Data Deleted",
            value=f"**{deletion_result['messages_deleted']:,}** messages removed",
            inline=False,
        )
        embed.add_field(
            name="📝 Going Forward",
            value=(
                "• Your future messages will **not** be logged\n"
                "• You can give consent again at any time using `/consent`"
            ),
            inline=False,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Cancel revocation"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Only the command user can cancel!", ephemeral=True
            )
            return

        # Disable buttons
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="✅ Cancelled. Your consent remains active.", view=self
        )

    async def on_timeout(self):
        """Handle timeout"""
        for item in self.children:
            item.disabled = True


class ConsentInfoView(discord.ui.View):
    """View with dropdown and button for consent"""

    def __init__(
        self,
        consent_service: ConsentService,
        user: discord.User,
        backfill_service: Optional[BackfillService] = None,
    ):
        super().__init__(timeout=300.0)  # 5 minute timeout
        self.consent_service = consent_service
        self.user = user
        self.backfill_service = backfill_service
        self.selected_level = ConsentLevel.CONTENT  # Default to Level 2
        self.backfill_historical = False  # Default to no backfill

    @discord.ui.select(
        placeholder="Choose your consent level (Default: Level 2 - Content)",
        options=[
            discord.SelectOption(
                label="Level 1 - Metadata Only",
                description="Log only timestamps and channels",
                value="1",
                emoji="📊",
            ),
            discord.SelectOption(
                label="Level 2 - Content (Recommended)",
                description="Metadata + message text",
                value="2",
                emoji="⭐",
                default=True,
            ),
            discord.SelectOption(
                label="Level 3 - Full",
                description="Content + attachments (images, files)",
                value="3",
                emoji="📦",
            ),
        ],
        row=0,
    )
    async def select_consent_level(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        """Handle consent level selection"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ Only the command user can select a level!", ephemeral=True
            )
            return

        self.selected_level = ConsentLevel(int(select.values[0]))

        # Acknowledge the interaction without sending a message
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="Collect past messages? (Default: No)",
        options=[
            discord.SelectOption(
                label="No - Only future messages",
                description="Only log messages sent after giving consent",
                value="false",
                emoji="⏩",
                default=True,
            ),
            discord.SelectOption(
                label="Yes - Include past messages",
                description="Collect all messages since you joined the server",
                value="true",
                emoji="📜",
            ),
        ],
        row=1,
    )
    async def select_backfill_option(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        """Handle backfill option selection"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ Only the command user can select this option!", ephemeral=True
            )
            return

        self.backfill_historical = select.values[0] == "true"

        # Acknowledge the interaction without sending a message
        await interaction.response.defer()

    @discord.ui.button(
        label="✅ Confirm Consent", style=discord.ButtonStyle.primary, row=2
    )
    async def consent_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Open the consent modal with username verification"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ Only the command user can confirm!", ephemeral=True
            )
            return

        modal = ConsentModal(
            self.consent_service,
            interaction.user.name,
            self.selected_level,
            self.backfill_service,
            self.backfill_historical,
        )
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        """Handle timeout"""
        for item in self.children:
            item.disabled = True

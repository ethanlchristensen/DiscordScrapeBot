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
        name="consent",
        description="Update your consent preferences (e.g., upgrade to Level 3 or enable backfill)",
    )
    async def give_consent(interaction: discord.Interaction):
        """Show consent information and modal to user

        Note: Messages are logged at Level 2 (Content) by default.
        Use this command to:
        - Upgrade to Level 3 (Full - includes attachments)
        - Enable retroactive message collection (backfill)
        - Update your preferences
        """
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server!", ephemeral=True
            )
            return

        # Show information about consent levels with a button to proceed
        embed = discord.Embed(
            title="🤖 Jade AI Data Collection - Update Preferences",
            description=(
                "📌 **Default Consent**: All users are granted Level 3 (Full) consent automatically.\n\n"
                "Use this command to change your consent level or enable retroactive message collection."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="📊 Consent Levels",
            value=(
                "**Level 1 - Metadata Only**\n"
                "└ Log when you send messages (timestamp, channel)\n\n"
                "**Level 2 - Content**\n"
                "└ Level 1 + your message text\n\n"
                "**Level 3 - Full** ⭐ Default\n"
                "└ Level 2 + attachments (images, files)\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔒 Your Privacy Rights",
            value=(
                "• All users are granted **Level 3 (Full)** consent by default\n"
                "• Use `/revoke_consent` to opt-out completely\n"
                "• Revoking deletes **all** your logged data\n"
                "• Bot messages are always logged (not affected by consent)"
            ),
            inline=False,
        )
        embed.set_footer(
            text="Select your preferences below to upgrade or enable backfill"
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

        # Get effective consent level (includes auto-consent)
        effective_level = await consent_service.get_effective_consent_level(
            interaction.guild.id, interaction.user.id
        )

        consent_record = await consent_service.get_user_consent(
            interaction.guild.id, interaction.user.id
        )

        # Check if user has explicitly revoked consent
        if effective_level == ConsentLevel.NONE:
            await interaction.response.send_message(
                "🚫 You have **opted out** of data collection.\n\n"
                "Your messages are **not being logged**.\n\n"
                "To opt back in, you would need to remove your opt-out status (contact an admin).",
                ephemeral=True,
            )
            return

            # Determine if consent was auto-granted or explicitly updated
        is_auto_granted = consent_record and consent_record.get("auto_granted", False)
        is_explicit = (
            consent_record is not None
            and consent_record.get("consent_active", False)
            and not is_auto_granted
        )

        if is_auto_granted:
            consent_type = "Auto-Granted (Default)"
        elif is_explicit:
            consent_type = "Explicitly Updated"
        else:
            consent_type = "Default"

        level_description = consent_service.get_consent_level_description(
            effective_level
        )

        embed = discord.Embed(
            title="✅ Your Consent Status",
            description=f"**Consent Type:** {consent_type}",
            color=discord.Color.green() if is_explicit else discord.Color.blue(),
        )
        embed.add_field(
            name="📊 Consent Level",
            value=f"**{effective_level.name}** (Level {effective_level.value})",
            inline=False,
        )
        embed.add_field(
            name="📝 What's Being Logged", value=level_description, inline=False
        )

        if consent_record and consent_record.get("consented_at"):
            timestamp_label = (
                "Auto-Granted On"
                if is_auto_granted
                else "Updated On"
                if is_explicit
                else "Consented On"
            )
            embed.add_field(
                name=f"📅 {timestamp_label}",
                value=f"<t:{int(consent_record['consented_at'].timestamp())}:F>",
                inline=False,
            )

        embed.add_field(
            name="💡 Options",
            value=(
                "• Use `/change_consent_level` to change your level (keeps your data)\n"
                "• Use `/consent` to enable backfill for historical messages\n"
                "• Use `/revoke_consent` to opt-out completely and delete all data"
            ),
            inline=False,
        )
        embed.set_footer(text="Auto-consent is enabled by default at Level 3 (Full)")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="change_consent_level",
        description="Change your consent level without deleting your data",
    )
    @app_commands.describe(
        level="Select your new consent level (1=Metadata, 2=Content, 3=Full)"
    )
    @app_commands.choices(
        level=[
            app_commands.Choice(name="Level 1 - Metadata Only", value=1),
            app_commands.Choice(name="Level 2 - Content (Message Text)", value=2),
            app_commands.Choice(name="Level 3 - Full (Content + Attachments)", value=3),
        ]
    )
    async def change_consent_level(interaction: discord.Interaction, level: int):
        """Change consent level without deleting existing data"""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server!", ephemeral=True
            )
            return

        # Get current consent
        consent_record = await consent_service.get_user_consent(
            interaction.guild.id, interaction.user.id
        )

        # Check if user has opted out
        if consent_record and not consent_record.get("consent_active", True):
            await interaction.response.send_message(
                "❌ You have opted out of data collection.\n\n"
                "Please contact an admin to restore your consent before changing levels.",
                ephemeral=True,
            )
            return

        new_level = ConsentLevel(level)
        current_level = await consent_service.get_effective_consent_level(
            interaction.guild.id, interaction.user.id
        )

        if current_level == new_level:
            await interaction.response.send_message(
                f"ℹ️ You are already at **{new_level.name}** (Level {new_level.value}).",
                ephemeral=True,
            )
            return

        # Update consent level (this updates the record without deleting data)
        await consent_service.grant_consent(
            guild_id=interaction.guild.id,
            guild_name=interaction.guild.name,
            user_id=interaction.user.id,
            user_name=interaction.user.name,
            consent_level=new_level,
            initials="UPDATED",
            backfill_historical=False,
        )

        level_description = consent_service.get_consent_level_description(new_level)

        # Determine if upgrade or downgrade
        change_type = "upgraded" if new_level > current_level else "downgraded"

        embed = discord.Embed(
            title=f"✅ Consent Level {change_type.title()}",
            description=f"Your consent level has been {change_type} successfully.",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Previous Level",
            value=f"**{current_level.name}** (Level {current_level.value})",
            inline=True,
        )
        embed.add_field(
            name="New Level",
            value=f"**{new_level.name}** (Level {new_level.value})",
            inline=True,
        )
        embed.add_field(
            name="📝 What Will Be Logged",
            value=level_description,
            inline=False,
        )
        embed.add_field(
            name="ℹ️ Important",
            value=(
                "• Your **existing data is preserved**\n"
                "• Future messages will be logged at the new level\n"
                "• Use `/revoke_consent` to delete all data and opt-out"
            ),
            inline=False,
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

        # Check effective consent level
        effective_level = await consent_service.get_effective_consent_level(
            interaction.guild.id, interaction.user.id
        )

        # If already revoked
        if effective_level == ConsentLevel.NONE:
            consent_record = await consent_service.get_user_consent(
                interaction.guild.id, interaction.user.id
            )
            revoked_at = consent_record.get("revoked_at") if consent_record else None

            message = "ℹ️ You have **already opted out** of data collection.\n\n"
            if revoked_at:
                message += f"Revoked on: <t:{int(revoked_at.timestamp())}:F>\n\n"
            message += "Your future messages are not being logged."

            await interaction.response.send_message(message, ephemeral=True)
            return

        # Show confirmation view
        view = RevokeConsentView(
            consent_service, interaction.guild.id, interaction.user.id
        )

        await interaction.response.send_message(
            "⚠️ **Are you sure you want to opt-out and delete your data?**\n\n"
            "This will:\n"
            "• **Stop logging** your future messages (opt-out from auto-consent)\n"
            "• **Permanently delete** all your previously logged messages and attachments\n"
            "• Mark your account as opted-out\n\n"
            "**This action cannot be undone!**\n\n"
            "Click the button below to confirm within 60 seconds.",
            view=view,
            ephemeral=True,
        )

    # Register commands
    tree.add_command(give_consent)
    tree.add_command(check_consent_status)
    tree.add_command(change_consent_level)
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

            # Get guild creation date for historical backfill
            # Using guild creation instead of user join date ensures we capture
            # all messages even if user left and rejoined
            guild_created_at = interaction.guild.created_at

            # Grant consent
            await self.consent_service.grant_consent(
                guild_id=interaction.guild.id,
                guild_name=interaction.guild.name,
                user_id=interaction.user.id,
                user_name=interaction.user.name,
                consent_level=self.consent_level,
                initials=entered_username,  # Store username instead of initials
                backfill_historical=self.backfill_historical,
                joined_at=guild_created_at,
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
            if self.backfill_historical and self.backfill_service:
                await interaction.followup.send(
                    "🔄 Starting retroactive message collection from guild creation... This may take a while depending on your message history.",
                    ephemeral=True,
                )

                try:
                    (
                        success,
                        failed,
                    ) = await self.backfill_service.backfill_user_messages(
                        guild=interaction.guild,
                        user_id=interaction.user.id,
                        after=guild_created_at,
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
        self.selected_level = ConsentLevel.FULL  # Default to Level 3
        self.backfill_historical = False  # Default to no backfill

    @discord.ui.select(
        placeholder="Choose your consent level (Default: Level 3 - Full)",
        options=[
            discord.SelectOption(
                label="Level 1 - Metadata Only",
                description="Log only timestamps and channels",
                value="1",
                emoji="📊",
            ),
            discord.SelectOption(
                label="Level 2 - Content",
                description="Metadata + message text",
                value="2",
                emoji="📝",
            ),
            discord.SelectOption(
                label="Level 3 - Full (Default)",
                description="Content + attachments (images, files)",
                value="3",
                emoji="⭐",
                default=True,
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
                description="Collect all messages since guild creation",
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

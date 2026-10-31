"""Config and options flow for GlowCast (UI only)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CUSTOM_GROUPS,
    CONF_CUSTOM_THEMES,
    CONF_EXCLUDED_LIGHTS,
    CONF_FAST_MIN_INTERVAL,
    CONF_SLOW_LIGHTS,
    CONF_SLOW_MIN_INTERVAL,
    CONF_TEAM_COLORS,
    DEFAULT_FAST_MIN_INTERVAL,
    DEFAULT_SLOW_MIN_INTERVAL,
    DOMAIN,
    EFFECTS,
)


class GlowCastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup — single instance."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="GlowCast", data={})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return GlowCastOptionsFlow()


class GlowCastOptionsFlow(config_entries.OptionsFlow):
    """Options: exclude lights, slow lights, intervals, team colors, custom themes/groups."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "custom_theme", "custom_group"],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            options = dict(self.config_entry.options)
            options.update(user_input)
            return self.async_create_entry(title="", data=options)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_EXCLUDED_LIGHTS,
                    default=list(opts.get(CONF_EXCLUDED_LIGHTS) or []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light", multiple=True)
                ),
                vol.Optional(
                    CONF_SLOW_LIGHTS,
                    default=list(opts.get(CONF_SLOW_LIGHTS) or []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light", multiple=True)
                ),
                vol.Optional(
                    CONF_SLOW_MIN_INTERVAL,
                    default=opts.get(CONF_SLOW_MIN_INTERVAL, DEFAULT_SLOW_MIN_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.5,
                        max=5.0,
                        step=0.1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_FAST_MIN_INTERVAL,
                    default=opts.get(CONF_FAST_MIN_INTERVAL, DEFAULT_FAST_MIN_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.1,
                        max=2.0,
                        step=0.05,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_TEAM_COLORS,
                    default=opts.get(CONF_TEAM_COLORS)
                    or [[0, 51, 160], [255, 255, 255]],
                ): selector.ObjectSelector(),
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_custom_theme(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Add a custom theme: name, 2-6 colors, effect."""
        if user_input is not None:
            options = dict(self.config_entry.options)
            themes = list(options.get(CONF_CUSTOM_THEMES) or [])
            name = user_input[CONF_NAME]
            key = name.lower().replace(" ", "_")
            colors = user_input.get("colors") or []
            if not isinstance(colors, list) or not (2 <= len(colors) <= 6):
                return self.async_show_form(
                    step_id="custom_theme",
                    data_schema=self._custom_theme_schema(),
                    errors={"colors": "colors_range"},
                )
            themes = [t for t in themes if t.get("key") != key]
            themes.append(
                {
                    "key": key,
                    "name": name,
                    "colors": colors,
                    "effect": user_input.get("effect", "static"),
                }
            )
            options[CONF_CUSTOM_THEMES] = themes
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="custom_theme", data_schema=self._custom_theme_schema()
        )

    def _custom_theme_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required("colors"): selector.ObjectSelector(),
                vol.Required("effect", default="static"): vol.In(list(EFFECTS)),
            }
        )

    async def async_step_custom_group(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            options = dict(self.config_entry.options)
            groups = list(options.get(CONF_CUSTOM_GROUPS) or [])
            name = user_input[CONF_NAME]
            gid = name.lower().replace(" ", "_")
            groups = [g for g in groups if g.get("id") != gid]
            groups.append(
                {
                    "id": gid,
                    "name": name,
                    "lights": user_input.get("lights") or [],
                }
            )
            options[CONF_CUSTOM_GROUPS] = groups
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required("lights"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light", multiple=True)
                ),
            }
        )
        return self.async_show_form(step_id="custom_group", data_schema=schema)

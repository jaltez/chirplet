from apps.api.prompting import DEFAULT_SYSTEM_PROMPT, build_system_prompt


class TestBuildSystemPrompt:
    def test_base_prompt_only(self):
        result = build_system_prompt(base_prompt="You are helpful.", include_time_context=False)
        assert result == "You are helpful."

    def test_with_persona(self):
        result = build_system_prompt(
            base_prompt="Base.",
            persona="a pirate captain",
            include_time_context=False,
        )
        assert "Persona: a pirate captain" in result
        assert "Base." in result

    def test_with_time_context(self):
        result = build_system_prompt(
            base_prompt="Base.",
            include_time_context=True,
        )
        assert "Current date:" in result
        assert "Current time:" in result
        assert "UTC" in result

    def test_without_time_context(self):
        result = build_system_prompt(
            base_prompt="Base.",
            include_time_context=False,
        )
        assert "Current date:" not in result
        assert result == "Base."

    def test_with_extra_context(self):
        result = build_system_prompt(
            base_prompt="Base.",
            extra_context="The user's name is Alice.",
            include_time_context=False,
        )
        assert "The user's name is Alice." in result

    def test_all_combined(self):
        result = build_system_prompt(
            base_prompt="Base.",
            persona="a friendly robot",
            include_time_context=True,
            extra_context="User speaks Spanish.",
        )
        assert "Base." in result
        assert "Persona: a friendly robot" in result
        assert "Current date:" in result
        assert "User speaks Spanish." in result

    def test_empty_persona_ignored(self):
        result = build_system_prompt(
            base_prompt="Base.",
            persona="   ",
            include_time_context=False,
        )
        assert "Persona:" not in result

    def test_empty_extra_context_ignored(self):
        result = build_system_prompt(
            base_prompt="Base.",
            extra_context="   ",
            include_time_context=False,
        )
        assert result == "Base."

    def test_default_prompt_is_valid_base(self):
        result = build_system_prompt(
            base_prompt=DEFAULT_SYSTEM_PROMPT,
            include_time_context=False,
        )
        assert result.strip() == DEFAULT_SYSTEM_PROMPT.strip()

"""Pydantic v2 config models for all YAML configuration files.

Each model is frozen (immutable) and provides a ``from_yaml`` classmethod
that reads a YAML file, parses it with ``yaml.safe_load``, and validates
through the Pydantic constructor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# scoring.yaml
# ---------------------------------------------------------------------------


class ScoringWeights(BaseModel):
    """Individual weight factors for composite score calculation.

    Attributes:
        score_velocity: Weight for HN score growth rate.
        comment_velocity: Weight for comment growth rate.
        front_page_presence: Weight for front-page endpoint count.
        recency: Weight for story age decay.
    """

    model_config = ConfigDict(frozen=True)

    score_velocity: float
    comment_velocity: float
    front_page_presence: float
    recency: float

    @field_validator("score_velocity", "comment_velocity", "front_page_presence", "recency")
    @classmethod
    def weights_must_be_non_negative(cls, v: float) -> float:
        """Ensure each weight is non-negative."""
        if v < 0:
            raise ValueError("Weight must be non-negative")
        return v


class ScoringConfig(BaseModel):
    """Configuration for the Scorer agent.

    Attributes:
        weights: Component weight factors that sum to 1.0.
        recency_decay: Mapping of hours -> decay score.
        front_page_scale: Mapping of endpoint count -> presence score.
        baseline_days: Trailing days for percentile normalization.
    """

    model_config = ConfigDict(frozen=True)

    weights: ScoringWeights
    recency_decay: dict[int, int]
    front_page_scale: dict[int, int]
    baseline_days: int = Field(gt=0)

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        """Load and validate scoring config from a YAML file.

        Args:
            path: Path to the scoring YAML configuration file.

        Returns:
            A validated ScoringConfig instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            pydantic.ValidationError: If the data fails validation.
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls(**data)


# ---------------------------------------------------------------------------
# orchestrator.yaml
# ---------------------------------------------------------------------------


class PriorityConfig(BaseModel):
    """Priority thresholds for orchestrator dispatch decisions.

    Attributes:
        min_composite_score: Minimum composite score to dispatch a story.
        ambiguity_range: Score range around threshold for LLM fallback.
    """

    model_config = ConfigDict(frozen=True)

    min_composite_score: int = Field(ge=0)
    ambiguity_range: int = Field(ge=0)


class BudgetConfig(BaseModel):
    """Token budget constraints for the orchestrator.

    Attributes:
        daily_token_budget: Estimated tokens available per day.
        tokens_per_article: Estimated token cost per article pipeline.
    """

    model_config = ConfigDict(frozen=True)

    daily_token_budget: int = Field(gt=0)
    tokens_per_article: int = Field(gt=0)


class OrchestratorLLMConfig(BaseModel):
    """LLM toggle for orchestrator relevance checks.

    Attributes:
        use_llm: Whether to use LLM for ambiguous prioritization.
    """

    model_config = ConfigDict(frozen=True)

    use_llm: bool = False


class OrchestratorConfig(BaseModel):
    """Configuration for the Orchestrator agent.

    Attributes:
        priority: Priority threshold settings.
        budget: Token budget settings.
        llm: LLM toggle for ambiguous decisions.
    """

    model_config = ConfigDict(frozen=True)

    priority: PriorityConfig
    budget: BudgetConfig
    llm: OrchestratorLLMConfig

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        """Load and validate orchestrator config from a YAML file.

        Args:
            path: Path to the orchestrator YAML configuration file.

        Returns:
            A validated OrchestratorConfig instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            pydantic.ValidationError: If the data fails validation.
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls(**data)


# ---------------------------------------------------------------------------
# llm.yaml
# ---------------------------------------------------------------------------


class LLMProviderConfig(BaseModel):
    """Per-provider LLM settings.

    Attributes:
        model: Model identifier string (e.g. ``gemini-2.5-flash``).
        base_url: Base URL for the provider API.
    """

    model_config = ConfigDict(frozen=True)

    model: str
    base_url: str


class RetryConfig(BaseModel):
    """Retry policy with exponential backoff for transient LLM errors.

    Attributes:
        max_retries: Maximum number of retry attempts.
        base_delay_seconds: Initial delay before the first retry.
        backoff_factor: Multiplier applied to delay after each retry.
        retryable_status_codes: HTTP status codes that trigger a retry.
    """

    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(ge=0)
    base_delay_seconds: float = Field(gt=0)
    backoff_factor: float = Field(gt=0)
    retryable_status_codes: list[int]


class LLMConfig(BaseModel):
    """Top-level LLM configuration with provider selection and shared settings.

    Attributes:
        provider: Active provider name (gemini, claude, openai, local).
        gemini: Gemini provider settings.
        claude: Claude provider settings.
        openai: OpenAI provider settings.
        local: Local/Ollama provider settings.
        max_tokens: Maximum tokens for LLM responses.
        temperature: Sampling temperature.
        timeout_seconds: Request timeout in seconds.
        retry: Retry policy configuration.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    gemini: LLMProviderConfig
    claude: LLMProviderConfig
    openai: LLMProviderConfig
    local: LLMProviderConfig
    max_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0.0, le=2.0)
    timeout_seconds: int = Field(gt=0)
    retry: RetryConfig

    @field_validator("provider")
    @classmethod
    def provider_must_be_known(cls, v: str) -> str:
        """Ensure provider is one of the supported values."""
        allowed = {"gemini", "claude", "openai", "local"}
        if v not in allowed:
            raise ValueError(f"Provider must be one of {allowed}, got {v!r}")
        return v

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        """Load and validate LLM config from a YAML file.

        Args:
            path: Path to the LLM YAML configuration file.

        Returns:
            A validated LLMConfig instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            pydantic.ValidationError: If the data fails validation.
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls(**data)


# ---------------------------------------------------------------------------
# categories.yaml
# ---------------------------------------------------------------------------


class CategoryRule(BaseModel):
    """Keyword and domain rules for a single category.

    Attributes:
        keywords: Keywords that indicate this category.
        domains: Web domains associated with this category.
    """

    model_config = ConfigDict(frozen=True)

    keywords: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class CategoriesConfig(BaseModel):
    """Configuration for the Categorizer agent.

    Attributes:
        categories: Mapping of category slug to its keyword/domain rules.
        hn_type_mappings: Mapping of HN story types to category slugs.
        default_category: Fallback category when no rules match.
    """

    model_config = ConfigDict(frozen=True)

    categories: dict[str, CategoryRule]
    hn_type_mappings: dict[str, str] = Field(default_factory=dict)
    default_category: str = "uncategorized"

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        """Load and validate categories config from a YAML file.

        Args:
            path: Path to the categories YAML configuration file.

        Returns:
            A validated CategoriesConfig instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            pydantic.ValidationError: If the data fails validation.
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls(**data)


# ---------------------------------------------------------------------------
# prompts.yaml
# ---------------------------------------------------------------------------


class PromptTemplate(BaseModel):
    """A system/user prompt pair for LLM interactions.

    Attributes:
        system: The system prompt template string.
        user: The user prompt template string with placeholders.
    """

    model_config = ConfigDict(frozen=True)

    system: str
    user: str


class PromptsConfig(BaseModel):
    """Configuration for all LLM prompt templates.

    Attributes:
        summarizer: Prompt template for initial summarization.
        summarizer_retry: Prompt template for retry summarization.
        validator: Prompt template for fact-check validation.
        orchestrator_relevance: Prompt template for relevance filtering.
        max_article_chars: Maximum characters of article text in prompts.
    """

    model_config = ConfigDict(frozen=True)

    summarizer: PromptTemplate
    summarizer_retry: PromptTemplate
    validator: PromptTemplate
    orchestrator_relevance: PromptTemplate
    max_article_chars: int = Field(gt=0)

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        """Load and validate prompts config from a YAML file.

        Args:
            path: Path to the prompts YAML configuration file.

        Returns:
            A validated PromptsConfig instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            pydantic.ValidationError: If the data fails validation.
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls(**data)

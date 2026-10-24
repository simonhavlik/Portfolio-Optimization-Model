"""
Portfolio Optimizer - AI-Powered Insights Engine
================================================

Integration with Large Language Models (LLMs) for intelligent portfolio
analysis, interpretation of optimization results, and generation of
actionable investment insights.

This module implements the Strategy pattern for LLM providers while
maintaining a consistent interface for portfolio analysis.

AI Applications in Portfolio Management:
----------------------------------------
1. Narrative generation from quantitative results
2. Risk interpretation and scenario analysis
3. Market regime identification
4. Factor exposure explanation
5. Rebalancing recommendations

Supported Providers:
--------------------
- DeepSeek (Default): DeepSeek-V3, DeepSeek-Coder
- OpenAI: GPT-4, GPT-3.5
- Any OpenAI-compatible API

Research Context:
-----------------
Recent advances in LLMs have enabled sophisticated financial analysis
through few-shot prompting and chain-of-thought reasoning. This
implementation leverages structured prompting techniques described in:

- Lopez-Lira & Tang (2023). Can ChatGPT Forecast Stock Price Movements?
- Kim et al. (2023). Large Language Models are Few-Shot Financial Reasoners
- Fabbri et al. (2023). SummEdits: A Dataset for Abstractive Summary Editing

References:
-----------
- DeepSeek API: https://platform.deepseek.com/
- OpenAI API: https://platform.openai.com/
"""

import json
import logging
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, asdict
from enum import Enum
import openai

from config import config

logger = logging.getLogger(__name__)


class AnalysisType(Enum):
    """Types of AI-generated analysis."""
    PORTFOLIO_SUMMARY = "portfolio_summary"
    RISK_ANALYSIS = "risk_analysis"
    OPTIMIZATION_EXPLANATION = "optimization_explanation"
    REBALANCING_ADVICE = "rebalancing_advice"
    MARKET_COMMENTARY = "market_commentary"


@dataclass
class PortfolioContext:
    """
    Structured context for LLM analysis.
    
    Implements the Data Transfer Object pattern for clean
    serialization to LLM prompts.
    """
    tickers: List[str]
    weights: Dict[str, float]
    expected_return: float
    volatility: float
    sharpe_ratio: float
    method: str
    risk_free_rate: float
    correlation_matrix: Optional[Dict] = None
    sector_allocation: Optional[Dict[str, float]] = None
    historical_performance: Optional[Dict] = None
    
    def to_prompt_context(self) -> str:
        """Convert to formatted string for LLM prompt."""
        return f"""
Portfolio Composition:
- Assets: {', '.join(self.tickers)}
- Optimization Method: {self.method}
- Expected Annual Return: {self.expected_return:.2%}
- Expected Volatility: {self.volatility:.2%}
- Sharpe Ratio: {self.sharpe_ratio:.3f}
- Risk-Free Rate Used: {self.risk_free_rate:.2%}

Asset Allocation:
{json.dumps({t: f"{w:.2%}" for t, w in self.weights.items()}, indent=2)}

Sector Allocation: {json.dumps(self.sector_allocation, indent=2) if self.sector_allocation else 'N/A'}
"""


class LLMClient:
    """
    Unified client for OpenAI-compatible LLM APIs.
    
    Implements the Adapter pattern for different LLM providers
    while maintaining consistent interface.
    
    Usage:
    ------
        client = LLMClient()
        response = client.complete("Analyze this portfolio...")
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ):
        """
        Initialize LLM client.
        
        Args:
            api_key: API key (defaults to config.LLM_API_KEY)
            base_url: API base URL (defaults to config.LLM_BASE_URL)
            model: Model identifier (defaults to config.LLM_MODEL)
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum response tokens
        """
        self.api_key = api_key or config.LLM_API_KEY
        self.base_url = base_url or config.LLM_BASE_URL
        self.model = model or config.LLM_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._explicit_key = api_key is not None
        self.client = None

        if self._explicit_key and self.api_key:
            self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

    def _active_client(self):
        """Resolve the switchable credential and keep the client in sync."""
        if not self._explicit_key:
            try:
                from services.api_keys import get_credential
                credential = get_credential("deepseek")
                key = credential.get("api_key")
                if key and (key != self.api_key or credential.get("base_url") != self.base_url
                            or credential.get("model") != self.model):
                    self.api_key = key
                    self.base_url = credential.get("base_url") or self.base_url
                    self.model = credential.get("model") or self.model
                    self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
            except Exception as exc:
                logger.warning("Could not resolve API credential: %s", exc)

        if self.client is None:
            if not self.api_key:
                raise RuntimeError("No LLM API key configured")
            self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self.client
    
    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Generate completion from LLM.
        
        Args:
            prompt: User prompt
            system_prompt: System instructions
            temperature: Override default temperature
            
        Returns:
            Generated text response
            
        Raises:
            Exception: On API error
        """
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            response = self._active_client().chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature or self.temperature,
                **({"max_tokens": max_tokens or self.max_tokens} if (max_tokens or self.max_tokens) else {}),
                extra_body={"reasoning_effort": "low"},
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"LLM API error: {e}")
            raise
    
    def chat(self, messages: list, temperature: float = 0.2,
             max_tokens: Optional[int] = None, model: Optional[str] = None) -> str:
        """Send a raw message list (needed for image inputs)."""
        response = self._active_client().chat.completions.create(
            model=model or self.model,
            messages=messages,
            temperature=temperature,
            **({"max_tokens": max_tokens or self.max_tokens} if (max_tokens or self.max_tokens) else {}),
            extra_body={"reasoning_effort": "low"},
        )
        return response.choices[0].message.content

    def analyze_json(self, prompt: str, schema: Dict) -> Dict:
        """
        Request structured JSON output from LLM.
        
        Uses few-shot prompting with JSON schema specification
        to ensure parseable output.
        
        Args:
            prompt: Analysis request
            schema: Expected JSON structure
            
        Returns:
            Parsed JSON response
        """
        json_prompt = f"""{prompt}

You must respond with valid JSON matching this schema:
{json.dumps(schema, indent=2)}

Respond ONLY with the JSON, no additional text."""
        
        response = self.complete(json_prompt, temperature=0.3)
        
        # Extract JSON from potential markdown code blocks
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            response = response.split("```")[1].split("```")[0]
        
        return json.loads(response.strip())




def _deterministic_review(holdings, metrics):
    """Fallback text built only from supplied figures."""
    ranked = sorted(holdings or [], key=lambda h: float(h.get("weight") or 0), reverse=True)
    top = ranked[:3]
    book = sum(float(h.get("weight") or 0) for h in ranked) or 1.0
    lines = []
    if len(top) >= 3:
        share = sum(float(h.get("weight") or 0) for h in top) / book
        lines.append(
            f"The three largest positions are {top[0].get('ticker')} at {top[0].get('weight', 0) * 100:.1f}%, "
            f"{top[1].get('ticker')} at {top[1].get('weight', 0) * 100:.1f}% and "
            f"{top[2].get('ticker')} at {top[2].get('weight', 0) * 100:.1f}%, "
            f"{share * 100:.1f}% of the book in three names."
        )
    if metrics:
        lines.append(
            f"Volatility is {metrics.get('volatility', 0) * 100:.1f}% with a Sharpe of "
            f"{metrics.get('sharpe_ratio', 0):.2f} and a maximum drawdown of "
            f"{metrics.get('max_drawdown', 0) * 100:.1f}%."
        )
        lines.append(
            f"95% CVaR is {metrics.get('cvar_95', 0) * 100:.1f}% of capital; "
            f"beta is {metrics.get('beta', 0):.2f}."
        )
    lines.append(
        "Concentration rather than the number of tickers drives the risk outcome; "
        "position sizing and the drawdown budget are the levers that matter."
    )
    return " ".join(lines).strip()


def _review_numbers_are_grounded(text, holdings, metrics):
    """Reject text containing figures that are not in the supplied data."""
    import re as _re
    allowed = set()

    def add(value):
        if isinstance(value, (int, float)):
            for formatted in (f"{value * 100:.1f}", f"{value * 100:.2f}", f"{value:.2f}", f"{value:.1f}"):
                allowed.add(formatted.strip("0").rstrip(".") if "." in formatted else formatted)
                allowed.add(formatted)

    for holding in holdings or []:
        add(float(holding.get("weight") or 0))
    for value in (metrics or {}).values():
        add(value)

    text = _re.sub(r"(two|three|four|five|six|seven|eight|nine|ten)", "", text, flags=_re.I)
    for token in _re.findall(r"\d+(?:\.\d+)?", text):
        normalised = token.rstrip("0").rstrip(".") if "." in token else token
        if token not in allowed and normalised not in allowed and not any(token in a for a in allowed):
            return False
    return True


class AIInsightsService:
    """
    High-level service for AI-powered portfolio analysis.
    
    Implements the Facade pattern providing business-level
    operations built on LLM capabilities.
    
    Usage:
    ------
        service = AIInsightsService()
        insights = service.generate_portfolio_summary(portfolio_context)
    """
    
    # System prompt template for financial analysis
    SYSTEM_PROMPT = """You are an expert quantitative portfolio manager and financial analyst with deep knowledge of Modern Portfolio Theory, risk management, and market dynamics.

Your analysis should be:
- Professional yet accessible to sophisticated investors
- Grounded in quantitative finance theory
- Actionable and specific
- Balanced in presenting both opportunities and risks

Avoid:
- Making specific price predictions
- Providing personalized investment advice
- Making guarantees about future performance
- Recommending specific buy/sell actions without context"""
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        Initialize AI insights service.
        
        Args:
            llm_client: Optional custom LLM client
        """
        self.llm = llm_client or LLMClient()
        self.enabled = config.ENABLE_AI_INSIGHTS
    
    def generate_portfolio_review(self, holdings: list, metrics: dict) -> str:
        """Dense, plain-language review. No filler, no repeated disclaimers."""
        if not self.enabled:
            return ""

        positions = ", ".join(
            f"{h.get('ticker')} {h.get('weight', 0) * 100:.1f}%"
            + (f" ({h['sector']})" if h.get("sector") else "")
            for h in holdings[:20]
        )
        figures = ", ".join(
            f"{key}={value}" for key, value in (metrics or {}).items() if value is not None
        )

        prompt = f"""Portfolio: {positions or 'n/a'}
Metrics: {figures or 'n/a'}

Write 6 to 10 sentences of portfolio commentary for a finance audience.

Requirements:
- Lead with the structural fact that matters most: concentration, sector tilt, or factor exposure.
- Quantify every claim with the numbers above or a percentage you can compute from the weights.
- Judge risk-adjusted quality, not just return.
- Give two concrete improvements with the trade-off each one creates.
- State what would make the thesis wrong.

Style:
- Dense, declarative, plain English or Czech (match the document language; default English).
- No headings, no bullet points, no lists, no markdown.
- No disclaimers, no "as an AI", no restating definitions, no generic advice.
- Never say "diversify" or "monitor" without a number attached."""

        if not holdings:
            return ""

        try:
            text = self.llm.complete(prompt, system_prompt=
                "You are a buy-side portfolio analyst writing internal notes. "
                "Every sentence must add information. Never introduce a figure that "
                "is not present in the input.", temperature=0.2).strip()
        except Exception as exc:
            logger.error("Portfolio review failed: %s", exc)
            return _deterministic_review(holdings, metrics)

        if not text or not _review_numbers_are_grounded(text, holdings, metrics):
            logger.warning("Review rejected by numeric guard; using deterministic text")
            return _deterministic_review(holdings, metrics)
        return text

    def generate_portfolio_summary(self, context: PortfolioContext) -> str:
        """
        Generate narrative summary of portfolio characteristics.
        
        Args:
            context: Portfolio data and metrics
            
        Returns:
            Markdown-formatted analysis text
        """
        if not self.enabled:
            return "AI insights are disabled. Configure LLM_API_KEY to enable."
        
        prompt = f"""Analyze the following optimized portfolio and provide a comprehensive summary:

{context.to_prompt_context()}

Please provide:
1. **Executive Summary** - Key characteristics in 2-3 sentences
2. **Risk-Return Profile** - Analysis of the Sharpe ratio and what it implies
3. **Diversification Assessment** - Evaluation of concentration risk
4. **Methodology Insights** - Why the {context.method} approach was suitable
5. **Key Considerations** - Important factors an investor should monitor

Format your response in Markdown with clear headers."""
        
        try:
            return self.llm.complete(prompt, system_prompt=self.SYSTEM_PROMPT)
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return f"Unable to generate AI insights: {str(e)}"
    
    def analyze_risk_factors(self, context: PortfolioContext) -> Dict[str, Any]:
        """
        Perform AI-driven risk factor analysis.
        
        Identifies potential risk exposures beyond standard metrics
        using LLM pattern recognition on portfolio structure.
        
        Args:
            context: Portfolio data
            
        Returns:
            Structured risk analysis
        """
        if not self.enabled:
            return {"error": "AI insights disabled"}
        
        prompt = f"""Analyze the risk factors for this portfolio:

{context.to_prompt_context()}

Identify:
1. Primary risk sources (market, sector, concentration)
2. Potential tail risks and black swan scenarios
3. Correlation breakdown risks
4. Macro sensitivity factors"""
        
        schema = {
            "risk_summary": "string - overall risk assessment in 1-2 sentences",
            "primary_risks": ["list of main risk factors"],
            "tail_risks": ["list of potential extreme scenarios"],
            "risk_score": "number 1-10 of overall risk level",
            "monitoring_recommendations": ["key metrics to watch"]
        }
        
        try:
            return self.llm.analyze_json(prompt, schema)
        except Exception as e:
            logger.error(f"Risk analysis failed: {e}")
            return {"error": str(e)}
    
    def explain_optimization_method(self, method: str, context: PortfolioContext) -> str:
        """
        Generate educational explanation of the optimization approach.
        
        Args:
            method: Optimization method name
            context: Portfolio context
            
        Returns:
            Educational explanation text
        """
        if not self.enabled:
            return ""
        
        method_descriptions = {
            "max_sharpe": "Maximum Sharpe Ratio (Tangency Portfolio)",
            "min_variance": "Global Minimum Variance",
            "mean_variance": "Mean-Variance Optimization (Markowitz)",
            "risk_parity": "Risk Parity / Equal Risk Contribution",
        }
        
        method_name = method_descriptions.get(method, method)
        
        prompt = f"""Explain the {method_name} optimization method used for this portfolio in accessible terms:

{context.to_prompt_context()}

Cover:
1. What problem this method solves
2. The mathematical intuition (without heavy equations)
3. When this approach is most appropriate
4. Limitations to be aware of

Target audience: Sophisticated retail investors with basic finance knowledge."""
        
        try:
            return self.llm.complete(prompt, system_prompt=self.SYSTEM_PROMPT, temperature=0.5)
        except Exception as e:
            logger.error(f"Method explanation failed: {e}")
            return ""
    
    def generate_rebalancing_advice(
        self,
        context: PortfolioContext,
        current_holdings: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        Generate rebalancing recommendations.
        
        Compares current holdings to optimal weights and provides
        transition analysis.
        
        Args:
            context: Target optimized portfolio
            current_holdings: Current portfolio weights
            
        Returns:
            Rebalancing recommendations
        """
        if not self.enabled:
            return {"error": "AI insights disabled"}
        
        prompt = f"""Compare current holdings to the optimized target portfolio:

Target Allocation:
{json.dumps(context.weights, indent=2)}

Current Holdings:
{json.dumps(current_holdings, indent=2)}

Provide rebalancing analysis including:
1. Significant deviations requiring attention
2. Tax-efficient rebalancing suggestions
3. Implementation considerations
4. Priority of trades (which to execute first)"""
        
        schema = {
            "summary": "string - overall rebalancing assessment",
            "significant_changes": [
                {"ticker": "symbol", "current": 0.0, "target": 0.0, "action": "buy/sell/hold"}
            ],
            "implementation_notes": ["practical considerations"],
            "priority": ["ordered list of recommended actions"]
        }
        
        try:
            return self.llm.analyze_json(prompt, schema)
        except Exception as e:
            logger.error(f"Rebalancing advice failed: {e}")
            return {"error": str(e)}


# Singleton instance
ai_service = AIInsightsService()

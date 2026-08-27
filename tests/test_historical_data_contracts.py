from dataclasses import FrozenInstanceError, asdict

import pytest

from jesse.exceptions import InvalidConfig
from jesse.services.historical_data import (
    AdjustmentMode,
    AssetClass,
    HistoricalCandle,
    HistoricalCandleBatch,
    HistoricalCandleDataset,
    HistoricalCandleProvider,
    HistoricalCandleProviderRegistry,
    HistoricalCandleRange,
    HistoricalCandleRequest,
    HistoricalDatasetStatus,
    HistoricalDataQualitySummary,
    HistoricalDataSourceType,
    InstrumentType,
    ProviderCapabilities,
)
from jesse.services.historical_data.errors import (
    HistoricalCandleValidationError,
    HistoricalDataRequestError,
    ProviderAuthenticationError,
    ProviderNotRegisteredError,
    ProviderQuotaError,
    ProviderRegistrationError,
    ProviderUnavailableError,
)
from jesse.services.simulation_assumptions import (
    Annualization,
    SimulationModel,
    legacy_type_from_simulation_model,
    resolve_annualization,
    resolve_simulation_model,
)


class BarsOnlyProvider(HistoricalCandleProvider):
    provider_id = 'bars-only'
    capabilities = ProviderCapabilities()

    def _fetch_candles(self, request: HistoricalCandleRequest) -> HistoricalCandleBatch:
        return HistoricalCandleBatch(
            request=request,
            candles=(
                HistoricalCandle(0, 10, 12, 9, 11, 100),
                # The missing middle minute remains absent by contract.
                HistoricalCandle(120_000, 13, 14, 12, 13.5, 50),
            ),
        )


def test_bars_only_provider_supports_sparse_candles_without_optional_capabilities():
    provider = BarsOnlyProvider()
    request = HistoricalCandleRequest(
        symbol='SPY',
        timeframe='1m',
        requested_range=HistoricalCandleRange(0, 180_000),
    )

    batch = provider.fetch_candles(request)

    assert [candle.timestamp for candle in batch.candles] == [0, 120_000]
    assert provider.capabilities.credential_validation is False
    assert provider.capabilities.ticker_search is False


def test_historical_candle_contract_is_immutable_and_validated():
    candle = HistoricalCandle(0, 10, 12, 9, 11, 100, vwap=10.5, transaction_count=4)

    with pytest.raises(FrozenInstanceError):
        candle.close = 12
    with pytest.raises(HistoricalCandleValidationError):
        HistoricalCandle(0, 10, 9, 8, 11, 100)
    with pytest.raises(HistoricalCandleValidationError):
        HistoricalCandle(0, 10, 12, 9, 11, -1)


def test_batch_rejects_duplicate_misaligned_and_out_of_range_timestamps():
    request = HistoricalCandleRequest('SPY', '1m', HistoricalCandleRange(0, 180_000))
    candle = HistoricalCandle(0, 10, 12, 9, 11, 100)

    with pytest.raises(HistoricalCandleValidationError, match='ascending and unique'):
        HistoricalCandleBatch(request, (candle, candle))
    with pytest.raises(HistoricalCandleValidationError, match='not aligned'):
        HistoricalCandleBatch(request, (HistoricalCandle(30_000, 10, 12, 9, 11, 100),))
    with pytest.raises(HistoricalCandleValidationError, match='outside'):
        HistoricalCandleBatch(request, (HistoricalCandle(180_000, 10, 12, 9, 11, 100),))


def test_batch_copies_mutable_provider_sequences_before_validation():
    request = HistoricalCandleRequest('SPY', '1m', HistoricalCandleRange(0, 180_000))
    provider_values = [HistoricalCandle(0, 10, 12, 9, 11, 100)]

    batch = HistoricalCandleBatch(request, provider_values)
    provider_values.append(HistoricalCandle(60_000, 11, 13, 10, 12, 100))

    assert len(batch.candles) == 1


def test_provider_capabilities_reject_unsupported_requests():
    provider = BarsOnlyProvider()

    with pytest.raises(HistoricalDataRequestError, match='timeframe'):
        provider.fetch_candles(HistoricalCandleRequest('SPY', '5m', HistoricalCandleRange(0, 300_000)))
    with pytest.raises(HistoricalDataRequestError, match='adjustment mode'):
        provider.fetch_candles(HistoricalCandleRequest(
            'SPY',
            '1m',
            HistoricalCandleRange(0, 180_000),
            AdjustmentMode.SPLIT_ADJUSTED,
        ))


def test_ready_dataset_requires_provider_provenance_and_is_deeply_immutable():
    dataset = HistoricalCandleDataset(
        id='dataset-1',
        source_type=HistoricalDataSourceType.PROVIDER,
        provider_id='massive-stocks',
        symbol='SPY',
        source_timeframe='1m',
        asset_class=AssetClass.EQUITY,
        instrument_type=InstrumentType.ETF,
        requested_range=HistoricalCandleRange(0, 180_000),
        imported_range=HistoricalCandleRange(0, 180_000),
        adjustment_mode=AdjustmentMode.SPLIT_ADJUSTED,
        status=HistoricalDatasetStatus.READY,
        row_count=2,
        checksum='fabricated-checksum',
        quality=HistoricalDataQualitySummary(unclassified_gap_count=1),
        created_at=1,
        updated_at=2,
        source_metadata={'provider_request_id': 'fabricated'},
    )

    assert dataset.source_metadata['provider_request_id'] == 'fabricated'
    with pytest.raises(TypeError):
        dataset.source_metadata['provider_request_id'] = 'changed'
    assert asdict(dataset)['source_metadata']['provider_request_id'] == 'fabricated'


def test_dataset_coerces_serialized_enums_before_enforcing_ready_invariants():
    with pytest.raises(HistoricalCandleValidationError, match='provider_id'):
        HistoricalCandleDataset(
            id='dataset-1',
            source_type='provider',
            symbol='SPY',
            source_timeframe='1m',
            asset_class='equity',
            instrument_type='etf',
            requested_range=HistoricalCandleRange(0, 180_000),
            adjustment_mode='split_adjusted',
            status='ready',
            row_count=2,
            quality=HistoricalDataQualitySummary(),
            created_at=1,
            updated_at=2,
        )


def test_provider_registry_rejects_duplicates_and_returns_typed_unknown_error():
    registry = HistoricalCandleProviderRegistry()
    provider = BarsOnlyProvider()

    registry.register(provider)

    assert registry.ids() == ('bars-only',)
    assert registry.get('bars-only') is provider
    with pytest.raises(ProviderRegistrationError, match='already registered'):
        registry.register(provider)
    with pytest.raises(ProviderNotRegisteredError) as exc_info:
        registry.get('missing')
    assert exc_info.value.code == 'provider_not_registered'


def test_provider_errors_have_stable_retry_semantics():
    assert ProviderAuthenticationError.code == 'provider_authentication_failed'
    assert ProviderAuthenticationError.retryable is False
    assert ProviderUnavailableError.code == 'provider_unavailable'
    assert ProviderUnavailableError.retryable is True
    assert ProviderQuotaError.code == 'provider_quota_exhausted'
    assert ProviderQuotaError.retryable is False


def test_simulation_model_normalizes_legacy_sessions_and_rejects_conflicts():
    assert resolve_simulation_model({'type': 'spot'}, 'futures') is SimulationModel.SPOT
    assert resolve_simulation_model({'type': ''}, 'spot') is SimulationModel.SPOT
    assert resolve_simulation_model(
        {'simulation_model': 'perpetual_futures', 'type': 'futures'},
        'spot',
    ) is SimulationModel.PERPETUAL_FUTURES
    assert legacy_type_from_simulation_model(SimulationModel.SPOT) == 'spot'

    with pytest.raises(InvalidConfig, match='Conflicting'):
        resolve_simulation_model({'simulation_model': 'spot', 'type': 'futures'}, 'spot')


def test_annualization_supports_only_the_two_product_assumptions():
    assert resolve_annualization({}) is Annualization.CALENDAR_365
    assert resolve_annualization({'annualization': '252'}) is Annualization.TRADING_252

    with pytest.raises(InvalidConfig, match='252 or 365'):
        resolve_annualization({'annualization': 360})
    with pytest.raises(InvalidConfig, match='252 or 365'):
        resolve_annualization({'annualization': 252.9})

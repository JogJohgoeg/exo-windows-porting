"""
Worker CLI entry point.

Run this on each non-coordinator machine to start a pipeline stage:

    python -m exo_windows_porting.distributed.worker_cli \
        --model meta-llama/Llama-2-7b-hf \
        --node-id node-b \
        --inference-port 29501 \
        --start-layer 16 \
        --end-layer 32 \
        --total-layers 32 \
        --next-host 127.0.0.1 \
        --coordinator-host 192.168.1.1 \
        --device cuda

For the LAST worker, omit --next-host (no next stage).
For the FIRST worker, add --is-first.
For the LAST worker, add --is-last.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Exo Windows Porting — pipeline worker node")
    p.add_argument("--model", required=True, help="HuggingFace model ID or local path")
    p.add_argument("--node-id", required=True, help="Unique node identifier")
    p.add_argument("--inference-port", type=int, required=True,
                   help="Port this worker binds its PULL socket on")
    p.add_argument("--start-layer", type=int, required=True,
                   help="First transformer layer (inclusive)")
    p.add_argument("--end-layer", type=int, required=True,
                   help="Last transformer layer (exclusive)")
    p.add_argument("--total-layers", type=int, required=True,
                   help="Total transformer layers in the full model")
    p.add_argument("--is-first", action="store_true",
                   help="This worker holds the token embedding table")
    p.add_argument("--is-last", action="store_true",
                   help="This worker holds norm + lm_head")
    p.add_argument("--next-host", default=None,
                   help="Hostname of the next pipeline stage (omit for last worker)")
    p.add_argument("--next-port", type=int, default=None,
                   help="Port of the next pipeline stage's PULL socket (omit for last worker)")
    p.add_argument("--coordinator-host", default="127.0.0.1",
                   help="Hostname of the coordinator (needed for last worker)")
    p.add_argument("--coordinator-results-port", type=int, default=29600,
                   help="Port the coordinator listens on for logits")
    p.add_argument("--device", default="cuda",
                   help="Torch device: cuda, cuda:1, cpu, …")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    from .shard import ModelShard
    from .worker import PipelineWorker

    shard = ModelShard(
        node_id=args.node_id,
        host="0.0.0.0",   # bind address (not used for routing)
        inference_port=args.inference_port,
        start_layer=args.start_layer,
        end_layer=args.end_layer,
        is_first=args.is_first,
        is_last=args.is_last,
        n_layers_total=args.total_layers,
    )

    worker = PipelineWorker(
        shard=shard,
        model_id=args.model,
        next_worker_host=args.next_host,
        next_worker_port=args.next_port,
        coordinator_host=args.coordinator_host,
        coordinator_results_port=args.coordinator_results_port,
        device=args.device,
    )

    await worker.start()
    logger.info("Worker %s running. Press Ctrl-C to stop.", args.node_id)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("Shutdown signal received")
        stop_event.set()

    # Register OS signal handlers where supported (Unix).
    # On Windows, loop.add_signal_handler() raises NotImplementedError for most
    # signals, so we fall back to the stdlib signal module for SIGINT only and
    # rely on KeyboardInterrupt for interactive Ctrl-C.
    _using_loop_signals = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
            _using_loop_signals = True
        except (NotImplementedError, OSError):
            pass  # Windows or unsupported signal

    run_task = asyncio.create_task(worker.run())

    if _using_loop_signals:
        await stop_event.wait()
    else:
        # Windows fallback: wait for the run task and catch KeyboardInterrupt
        try:
            await run_task
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            stop_event.set()

    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass

    await worker.stop()
    logger.info("Worker %s stopped cleanly.", args.node_id)


def run() -> None:
    """Synchronous entry point for the ``exo-worker`` console script.

    Console scripts installed by pip call a plain function — they cannot call
    an async coroutine directly.  This wrapper bridges the gap.
    """
    asyncio.run(main())


if __name__ == "__main__":
    run()

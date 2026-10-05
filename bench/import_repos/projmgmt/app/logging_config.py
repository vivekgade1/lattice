# ruff: noqa
"""Configure application-wide logging using Loguru.

This module integrates Python's standard logging with Loguru to provide
structured, colorized, and consistent log output across the application.
"""

import logging
import sys

from loguru import logger


class InterceptHandler(logging.Handler):
    """A logging handler that redirects standard logging records to Loguru.

    This handler allows Python's built-in logging module to seamlessly
    integrate with Loguru, preserving log levels, exception information,
    and module context.

    Methods:
        emit(record): Process a logging.LogRecord and forward it to Loguru.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Redirect a standard logging record to Loguru.

        This method is called automatically by the logging framework for each
        log record. It attempts to map the standard logging level to the
        corresponding Loguru level, binds the module name, and logs the
        message with exception info if present.

        Args:
            record (logging.LogRecord): The log record to be processed.
        """
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelname

        logger.opt(
            depth=6,
            exception=record.exc_info,
        ).bind(module=record.name).log(level, record.getMessage())


def setup_logging() -> None:
    """Configure Loguru and standard logging to work together."""
    logger.remove()

    logger.add(
        sys.stdout,
        colorize=True,
        format='<green>{time:YYYY-MM-DD HH:mm:ss}</green> | '
        '<level>{level: <8}</level> | '
        '<cyan>{extra[module]}</cyan> | '
        '<level>{message}</level>',
        level='INFO',
        enqueue=True,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

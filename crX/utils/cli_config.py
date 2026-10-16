"""Module to parse arguments from CLI"""

from argparse import ArgumentParser, Namespace


def arg_parser() -> Namespace:
    """Function to parse arguments

    Returns
    -------
        Namespace: populated namespace from arguments

    """
    parser = ArgumentParser(description="从命令行启动 crX")
    parser.add_argument(
        "--start",
        "-s",
        dest="start",
        action="store_true",
        help="打开窗口后自动启动任务",
    )
    return parser.parse_args()

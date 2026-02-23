"""CLI 参数解析 - 参考 src/model/arg.rs"""

import click


@click.command()
@click.option("-c", "--config", "config_path", default=None, help="配置文件路径")
@click.option("--credentials", "credentials_path", default=None, help="凭证文件路径")
def parse_args(config_path, credentials_path):
    """Anthropic <-> Kiro API 客户端"""
    return config_path, credentials_path


def get_args():
    """解析命令行参数，返回 (config_path, credentials_path)"""
    import sys
    config_path = None
    credentials_path = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("-c", "--config") and i + 1 < len(args):
            config_path = args[i + 1]
            i += 2
        elif args[i] == "--credentials" and i + 1 < len(args):
            credentials_path = args[i + 1]
            i += 2
        else:
            i += 1
    return config_path, credentials_path

if __name__ == '__main__':
    from config import config
    from ok import OK

    ok = OK(config)
    ok.start()

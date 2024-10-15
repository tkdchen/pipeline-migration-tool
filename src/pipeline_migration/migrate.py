import argparse


def main():
    parser = argparse.ArgumentParser(description="Pipeline migration tool for Konflux CI.")

    parser.add_argument("-f", required=True)
    parser.add_argument("-t", required=True)
    parser.add_argument("-p", required=True)

    args = parser.parse_args()

    print(args)

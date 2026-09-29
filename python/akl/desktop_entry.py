"""先分派冻结应用的多进程工作者，再加载 GUI，避免递归启动窗口。"""
import multiprocessing
import os
import sys

if __name__ == '__main__':
    # Windows 的无控制台应用中这两个流可能为 None；工作进程仍会输出解析进度。
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    multiprocessing.freeze_support()
    os.environ['MPLBACKEND'] = 'Agg'
    if '--render' in sys.argv:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--render', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--jobs', type=int, default=4)
        parser.add_argument('--clock-mhz', type=float)
        parser.add_argument('--log-file')
        args = parser.parse_args()
        if args.log_file:
            sys.stdout = sys.stderr = open(args.log_file, 'w', encoding='utf-8', buffering=1)
        from akl.report_bundle import convert
        try:
            convert(args.render, args.output, args.jobs, args.clock_mhz)
        except Exception as error:
            print(f'{type(error).__name__}: {error}', flush=True)
            sys.exit(1)
    else:
        from akl.desktop import main
        sys.exit(main())

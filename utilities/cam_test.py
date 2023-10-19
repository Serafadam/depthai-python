#!/usr/bin/env python3

"""
Use 'T' to trigger autofocus, 'IOKL,.'
for manual exposure/focus:
  Control:      key[dec/inc]  min..max
  exposure time:     I   O      1..33000 [us]
  sensitivity iso:   K   L    100..1600
  focus:             ,   .      0..255 [far..near]
To go back to auto controls:
  'E' - autoexposure
  'F' - autofocus (continuous)

Other controls:
'1' - AWB lock (true / false)
'2' - AE lock (true / false)
'3' - Select control: AWB mode
'4' - Select control: AE compensation
'5' - Select control: anti-banding/flicker mode
'6' - Select control: effect mode
'7' - Select control: brightness
'8' - Select control: contrast
'9' - Select control: saturation
'0' - Select control: sharpness
'[' - Select control: luma denoise
']' - Select control: chroma denoise
'a' 'd' - Increase/decrease dot projector intensity
'w' 's' - Increase/decrease flood LED intensity

For the 'Select control: ...' options, use these keys to modify the value:
  '-' or '_' to decrease
  '+' or '=' to increase

'/' to toggle printing camera settings: exposure, ISO, lens position, color temperature
"""

import depthai as dai
import os
# os.environ["DEPTHAI_LEVEL"] = "debug"

import cv2
import numpy as np
import argparse
import collections
import time
from itertools import cycle
from pathlib import Path
import sys
import signal


def socket_type_pair(arg):
    socket, type = arg.split(',')
    if not (socket in ['rgb', 'left', 'right', 'cama', 'camb', 'camc', 'camd', 'came', 'camf', 'camg', 'camh']):
        raise ValueError("")
    if not (type in ['m', 'mono', 'c', 'color', 't', 'tof']):
        raise ValueError("")
    is_color = True if type in ['c', 'color'] else False
    is_tof = True if type in ['t', 'tof'] else False
    return [socket, is_color, is_tof]


parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('-cams', '--cameras', type=socket_type_pair, nargs='+',
                    default=[],
                    help="Which camera sockets to enable, and type: c[olor] / m[ono] / t[of]. "
                    "E.g: -cams rgb,m right,c . If not specified, all connected cameras will be used.")
parser.add_argument('-mres', '--mono-resolution', type=int, default=800, choices={480, 400, 720, 800},
                    help="Select mono camera resolution (height). Default: %(default)s")
parser.add_argument('-cres', '--color-resolution', default='1080', choices={'720', '800', '1080', '1200', '4k', '5mp', '12mp', '13mp', '48mp'},
                    help="Select color camera resolution / height. Default: %(default)s")
parser.add_argument('-rot', '--rotate', const='all', choices={'all', 'rgb', 'mono'}, nargs="?",
                    help="Which cameras to rotate 180 degrees. All if not filtered")
parser.add_argument('-fps', '--fps', type=float, default=30,
                    help="FPS to set for all cameras")
parser.add_argument('-isp3afps', '--isp3afps', type=int, default=0,
                    help="3A FPS to set for all cameras")
parser.add_argument('-ds', '--isp-downscale', default=1, type=int,
                    help="Downscale the ISP output by this factor")
parser.add_argument('-rs', '--resizable-windows', action='store_true',
                    help="Make OpenCV windows resizable. Note: may introduce some artifacts")
parser.add_argument('-tun', '--camera-tuning', type=Path,
                    help="Path to custom camera tuning database")
parser.add_argument('-raw', '--enable-raw', default=False, action="store_true",
                    help='Enable the RAW camera streams')
parser.add_argument('-tofraw', '--tof-raw', action='store_true',
                    help="Show just ToF raw output instead of post-processed depth")
parser.add_argument('-tofamp', '--tof-amplitude', action='store_true',
                    help="Show also ToF amplitude output alongside depth")
parser.add_argument('-tofcm', '--tof-cm', action='store_true',
                    help="Show ToF depth output in centimeters, capped to 255")
parser.add_argument('-tofmedian', '--tof-median', choices=[0,3,5,7], default=5, type=int,
                    help="ToF median filter kernel size")
parser.add_argument('-rgbprev', '--rgb-preview', action='store_true',
                    help="Show RGB `preview` stream instead of full size `isp`")

parser.add_argument('-d', '--device', default="", type=str,
                    help="Optional MX ID of the device to connect to.")

parser.add_argument('-ctimeout', '--connection-timeout', default=30000,
                    help="Connection timeout in ms. Default: %(default)s (sets DEPTHAI_CONNECTION_TIMEOUT environment variable)")

parser.add_argument('-btimeout', '--boot-timeout', default=30000,
                    help="Boot timeout in ms. Default: %(default)s (sets DEPTHAI_BOOT_TIMEOUT environment variable)")

parser.add_argument('--stress', action='store_true',
                    help="Run stress test. This will override all other options (except -d/--device) and will run a heavy pipeline until the user stops it.")

parser.add_argument("--no-stereo", action="store_true",
                    help="Don't create a stereo depth node if the device has a stereo pair.")

parser.add_argument("--gui", action="store_true",
                    help="Use GUI instead of CLI")
parser.add_argument("-h", "--help", action="store_true", default=False,
                    help="Show this help message and exit") # So you can forward --help to stress test, without it being consumed by cam_test.py

args, _unknown = parser.parse_known_args()

# Set timeouts before importing depthai
os.environ["DEPTHAI_CONNECTION_TIMEOUT"] = str(args.connection_timeout)
os.environ["DEPTHAI_BOOT_TIMEOUT"] = str(args.boot_timeout)

if args.stress:
    from stress_test import stress_test
    stress_test(args.device)
    exit(0)

if args.help:
    parser.print_help()
    exit(0)

if args.gui:
    import cam_test_gui
    cam_test_gui.main()

print("DepthAI version:", dai.__version__)
print("DepthAI path:", dai.__file__)

cam_socket_opts = {
    'rgb': dai.CameraBoardSocket.CAM_A,
    'left': dai.CameraBoardSocket.CAM_B,
    'right': dai.CameraBoardSocket.CAM_C,
    'cama': dai.CameraBoardSocket.CAM_A,
    'camb': dai.CameraBoardSocket.CAM_B,
    'camc': dai.CameraBoardSocket.CAM_C,
    'camd': dai.CameraBoardSocket.CAM_D,
    'came': dai.CameraBoardSocket.CAM_E,
    'camf': dai.CameraBoardSocket.CAM_F,
    'camg': dai.CameraBoardSocket.CAM_G,
    'camh': dai.CameraBoardSocket.CAM_H,
}

rotate = {
    'rgb': args.rotate in ['all', 'rgb'],
    'left': args.rotate in ['all', 'mono'],
    'right': args.rotate in ['all', 'mono'],
    'cama': args.rotate in ['all', 'rgb'],
    'camb': args.rotate in ['all', 'mono'],
    'camc': args.rotate in ['all', 'mono'],
    'camd': args.rotate in ['all', 'rgb'],
    'came': args.rotate in ['all', 'mono'],
    'camf': args.rotate in ['all', 'mono'],
    'camg': args.rotate in ['all', 'mono'],
    'camh': args.rotate in ['all', 'mono'],
}

mono_res_opts = {
    400: dai.MonoCameraProperties.SensorResolution.THE_400_P,
    480: dai.MonoCameraProperties.SensorResolution.THE_480_P,
    720: dai.MonoCameraProperties.SensorResolution.THE_720_P,
    800: dai.MonoCameraProperties.SensorResolution.THE_800_P,
    1200: dai.MonoCameraProperties.SensorResolution.THE_1200_P,
}

color_res_opts = {
    '720':  dai.ColorCameraProperties.SensorResolution.THE_720_P,
    '800':  dai.ColorCameraProperties.SensorResolution.THE_800_P,
    '1080': dai.ColorCameraProperties.SensorResolution.THE_1080_P,
    '1200': dai.ColorCameraProperties.SensorResolution.THE_1200_P,
    '4k':   dai.ColorCameraProperties.SensorResolution.THE_4_K,
    '5mp': dai.ColorCameraProperties.SensorResolution.THE_5_MP,
    '12mp': dai.ColorCameraProperties.SensorResolution.THE_12_MP,
    '13mp': dai.ColorCameraProperties.SensorResolution.THE_13_MP,
    '48mp': dai.ColorCameraProperties.SensorResolution.THE_48_MP,
}


def clamp(num, v0, v1):
    return max(v0, min(num, v1))

# Calculates FPS over a moving window, configurable


class FPS:
    def __init__(self, window_size=30):
        self.dq = collections.deque(maxlen=window_size)
        self.fps = 0

    def update(self, timestamp=None):
        if timestamp == None:
            timestamp = time.monotonic()
        count = len(self.dq)
        if count > 0:
            self.fps = count / (timestamp - self.dq[0])
        self.dq.append(timestamp)

    def get(self):
        return self.fps


def exit_cleanly(signum, frame):
    print("Exiting cleanly")
    cv2.destroyAllWindows()
    sys.exit(0)


def socket_to_socket_opt(socket: dai.CameraBoardSocket) -> str:
    return str(socket).split('.')[-1].replace("_", "").lower()


signal.signal(signal.SIGINT, exit_cleanly)

# Connect to device, so that we can get connected cameras in case of no args
success, device = dai.Device.getDeviceByMxId(args.device)
dai_device_args = []
if success:
    dai_device_args.append(device)
with dai.Device(*dai_device_args) as device:
    cam_list = []
    cam_type_color = {}
    cam_type_tof = {}
    print("Enabled cameras:")

    if not args.cameras:
        connected_cameras = device.getConnectedCameraFeatures()
        args.cameras = [(socket_to_socket_opt(cam.socket), cam.supportedTypes[0] ==
                         dai.CameraSensorType.COLOR, cam.supportedTypes[0] == dai.CameraSensorType.TOF) for cam in connected_cameras]

    for socket, is_color, is_tof in args.cameras:
        cam_list.append(socket)
        cam_type_color[socket] = is_color
        cam_type_tof[socket] = is_tof
        print((socket).rjust(7), ':',
              'tof' if is_tof else 'color' if is_color else 'mono')

    # Start defining a pipeline
    pipeline = dai.Pipeline()
    # Uncomment to get better throughput
    # pipeline.setXLinkChunkSize(0)

    control = pipeline.createXLinkIn()
    control.setStreamName('control')

    xinTofConfig = pipeline.createXLinkIn()
    xinTofConfig.setStreamName('tofConfig')

    cam = {}
    tof = {}
    xout = {}
    xout_raw = {}
    xout_tof_amp = {}
    streams = []
    tofConfig = {}
    for c in cam_list:
        tofEnableRaw = False
        xout[c] = pipeline.createXLinkOut()
        xout[c].setStreamName(c)
        streams.append(c)
        if cam_type_tof[c]:
            cam[c] = pipeline.create(dai.node.ColorCamera)  # .Camera
            if args.tof_raw:
                tofEnableRaw = True
            else:
                tof[c] = pipeline.create(dai.node.ToF)
                cam[c].raw.link(tof[c].input)
                tof[c].depth.link(xout[c].input)
                xinTofConfig.out.link(tof[c].inputConfig)
                tofConfig = tof[c].initialConfig.get()
                tofConfig.depthParams.freqModUsed = dai.RawToFConfig.DepthParams.TypeFMod.MIN
                tofConfig.depthParams.avgPhaseShuffle = False
                tofConfig.depthParams.minimumAmplitude = 3.0
                tof[c].initialConfig.set(tofConfig)
                if args.tof_amplitude:
                    amp_name = 'tof_amplitude_' + c
                    xout_tof_amp[c] = pipeline.create(dai.node.XLinkOut)
                    xout_tof_amp[c].setStreamName(amp_name)
                    streams.append(amp_name)
                    tof[c].amplitude.link(xout_tof_amp[c].input)
        elif cam_type_color[c]:
            cam[c] = pipeline.createColorCamera()
            cam[c].setResolution(color_res_opts[args.color_resolution])
            cam[c].setIspScale(1, args.isp_downscale)
            # cam[c].initialControl.setManualFocus(85) # TODO
            if args.rgb_preview:
                cam[c].preview.link(xout[c].input)
            else:
                cam[c].isp.link(xout[c].input)
        else:
            cam[c] = pipeline.createMonoCamera()
            cam[c].setResolution(mono_res_opts[args.mono_resolution])
            cam[c].out.link(xout[c].input)
        cam[c].setBoardSocket(cam_socket_opts[c])
        # Num frames to capture on trigger, with first to be discarded (due to degraded quality)
        # cam[c].initialControl.setExternalTrigger(2, 1)
        # cam[c].initialControl.setStrobeExternal(48, 1)
        # cam[c].initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.INPUT)

        # cam[c].initialControl.setManualExposure(15000, 400) # exposure [us], iso
        # When set, takes effect after the first 2 frames
        # cam[c].initialControl.setManualWhiteBalance(4000)  # light temperature in K, 1000..12000
        control.out.link(cam[c].inputControl)
        if rotate[c]:
            cam[c].setImageOrientation(
                dai.CameraImageOrientation.ROTATE_180_DEG)
        cam[c].setFps(args.fps)
        if args.isp3afps:
            cam[c].setIsp3aFps(args.isp3afps)

        if args.enable_raw or tofEnableRaw:
            raw_name = 'raw_' + c
            xout_raw[c] = pipeline.create(dai.node.XLinkOut)
            xout_raw[c].setStreamName(raw_name)
            streams.append(raw_name)
            cam[c].raw.link(xout_raw[c].input)
            cam[c].setRawOutputPacked(False)

    if args.camera_tuning:
        pipeline.setCameraTuningBlobPath(str(args.camera_tuning))

    stereo = None

    if args.no_stereo:
        print("--no-stereo specified, skipping stereo depth creation")
    else:
        try:
            try:
                calib = device.readCalibration2()
            except:
                raise Exception("Device is not calibrated.")
            eeprom = calib.getEepromData()
            left, right = eeprom.stereoRectificationData.leftCameraSocket, eeprom.stereoRectificationData.rightCameraSocket

            # Get the actual camera nodes
            # The cameras may have been specified with -cams rgb,c left,m right,m kind of names, so we need to handle these edge cases
            left_sock_opt = socket_to_socket_opt(left)
            right_sock_opt = socket_to_socket_opt(right)
            left_cam = cam.get(left_sock_opt, None)
            right_cam = cam.get(right_sock_opt, None)
            if not left_cam:
                if left == dai.CameraBoardSocket.CAM_A:
                    left_sock_opt = "rgb"
                elif left == dai.CameraBoardSocket.CAM_B:
                    left_sock_opt = "left"
                elif left == dai.CameraBoardSocket.CAM_C:
                    left_sock_opt = "right"
                left_cam = cam.get(left_sock_opt, None)
            if not right_cam:
                if right == dai.CameraBoardSocket.CAM_A:
                    right_sock_opt = "rgb"
                elif right == dai.CameraBoardSocket.CAM_B:
                    right_sock_opt = "left"
                elif right == dai.CameraBoardSocket.CAM_C:
                    right_sock_opt = "right"
                right_cam = cam.get(right_sock_opt, None)

            if left_cam and right_cam:
                cam_features = device.getConnectedCameraFeatures()
                left_cam_features = next(
                    filter(lambda c: c.socket == left, cam_features))
                right_cam_features = next(
                    filter(lambda c: c.socket == right, cam_features))
                if left_cam_features.width > 1280:
                    if args.isp_downscale == 1:
                        raise Exception(
                            "Can't create stereo depth with left cam width > 1280. Use --isp-downscale to downscale the image.")
                if right_cam_features.width > 1280:
                    if args.isp_downscale == 1:
                        raise Exception(
                            "Can't create stereo depth with right cam width > 1280. Use --isp-downscale to downscale the image.")
                left_out = "out"
                right_out = "out"
                if cam_type_color[left_sock_opt]:
                    left_out = "video"
                if cam_type_color[right_sock_opt]:
                    right_out = "video"

                print(
                    "Device is calibrated and has a stereo pair, creating StereoDepth node.")
                stereo = pipeline.createStereoDepth()
                stereo.setDefaultProfilePreset(
                    dai.node.StereoDepth.PresetMode.HIGH_ACCURACY)
                stereo.setLeftRightCheck(True)
                stereo.setSubpixel(True)
                getattr(left_cam, left_out).link(stereo.left)
                getattr(right_cam, right_out).link(stereo.right)
                xout_stereo = pipeline.createXLinkOut()
                depth_stream = "stereo_depth"
                xout_stereo.setStreamName(depth_stream)
                stereo.depth.link(xout_stereo.input)
                streams.append(depth_stream)
        except Exception as e:
            print("Couldn't create depth:", e)

    # Pipeline is defined, now we can start it
    device.startPipeline(pipeline)

    # print('Connected cameras:', [c.name for c in device.getConnectedCameras()])
    print('Connected cameras:')
    cam_name = {}
    for p in device.getConnectedCameraFeatures():
        print(
            f' -socket {p.socket.name:6}: {p.sensorName:6} {p.width:4} x {p.height:4} focus:', end='')
        print('auto ' if p.hasAutofocus else 'fixed', '- ', end='')
        print(*[type.name for type in p.supportedTypes])
        cam_name[p.socket.name] = p.sensorName
        if args.enable_raw:
            cam_name['raw_'+p.socket.name] = p.sensorName
        if args.tof_amplitude:
            cam_name['tof_amplitude_'+p.socket.name] = p.sensorName

    print('USB speed:', device.getUsbSpeed().name)

    print('IR drivers:', device.getIrDrivers())

    q = {}
    fps_host = {}  # FPS computed based on the time we receive frames in app
    fps_capt = {}  # FPS computed based on capture timestamps from device
    for c in streams:
        q[c] = device.getOutputQueue(name=c, maxSize=4, blocking=False)
        # The OpenCV window resize may produce some artifacts
        if args.resizable_windows:
            cv2.namedWindow(c, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(c, (640, 480))
        fps_host[c] = FPS()
        fps_capt[c] = FPS()

    controlQueue = device.getInputQueue('control')
    tofCfgQueue = device.getInputQueue('tofConfig')

    # Manual exposure/focus set step
    EXP_STEP = 500  # us
    ISO_STEP = 50
    LENS_STEP = 3
    DOT_STEP = 100
    FLOOD_STEP = 100
    DOT_MAX = 1200
    FLOOD_MAX = 1500

    # Defaults and limits for manual focus/exposure controls
    lensPos = 150
    lensMin = 0
    lensMax = 255

    expTime = 20000
    expMin = 1
    expMax = 33000

    sensIso = 800
    sensMin = 100
    sensMax = 1600

    dotIntensity = 0
    floodIntensity = 0

    awb_mode = cycle([item for name, item in vars(
        dai.CameraControl.AutoWhiteBalanceMode).items() if name.isupper()])
    anti_banding_mode = cycle([item for name, item in vars(
        dai.CameraControl.AntiBandingMode).items() if name.isupper()])
    effect_mode = cycle([item for name, item in vars(
        dai.CameraControl.EffectMode).items() if name.isupper()])

    ae_comp = 0
    ae_lock = False
    awb_lock = False
    saturation = 0
    contrast = 0
    brightness = 0
    sharpness = 0
    luma_denoise = 0
    chroma_denoise = 0
    control = 'none'
    show = False

    jet_custom = cv2.applyColorMap(
        np.arange(256, dtype=np.uint8), cv2.COLORMAP_JET)
    jet_custom[0] = [0, 0, 0]

    print("Cam:", *['     ' + c.ljust(8)
                    for c in cam_list], "[host | capture timestamp]")

    capture_list = []
    while True:
        for c in streams:
            try:
                pkt = q[c].tryGet()
            except Exception as e:
                print(e)
                exit_cleanly(0, 0)
            if pkt is not None:
                fps_host[c].update()
                fps_capt[c].update(pkt.getTimestamp().total_seconds())
                width, height = pkt.getWidth(), pkt.getHeight()
                frame = pkt.getCvFrame()
                cam_skt = c.split('_')[-1]

                if c == "stereo_depth":
                    depth_downscaled = frame[::4]
                    try:
                        min_depth = np.percentile(
                            depth_downscaled[depth_downscaled != 0], 1)
                        max_depth = np.percentile(depth_downscaled, 99)
                    except IndexError:
                        continue
                    depth_frame_color = np.interp(
                        frame, (min_depth, max_depth), (0, 255)).astype(np.uint8)
                    depth_frame_color = cv2.applyColorMap(
                        depth_frame_color, cv2.COLORMAP_HOT)
                    cv2.imshow(c, depth_frame_color)
                    continue

                if cam_type_tof[cam_skt] and not (c.startswith('raw_') or c.startswith('tof_amplitude_')):
                    if args.tof_cm:
                        # pixels represent `cm`, capped to 255. Value can be checked hovering the mouse
                        frame = (frame // 10).clip(0, 255).astype(np.uint8)
                    else:
                        frame = (frame.view(np.int16).astype(float))
                        frame = cv2.normalize(
                            frame, frame, alpha=255, beta=0, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8U)
                        frame = cv2.applyColorMap(frame, jet_custom)
                if show:
                    txt = f"[{c:5}, {pkt.getSequenceNum():4}] "
                    txt += f"Exp: {pkt.getExposureTime().total_seconds()*1000:6.3f} ms, "
                    txt += f"ISO: {pkt.getSensitivity():4}, "
                    txt += f"Lens pos: {pkt.getLensPosition():3}, "
                    txt += f"Color temp: {pkt.getColorTemperature()} K"
                    print(txt)
                capture = c in capture_list
                if capture:
                    capture_file_info = ('capture_' + c + '_' + cam_name[cam_socket_opts[cam_skt].name]
                                         + '_' + str(width) + 'x' + str(height)
                                         + '_exp_' +
                                         str(int(
                                             pkt.getExposureTime().total_seconds()*1e6))
                                         + '_iso_' + str(pkt.getSensitivity())
                                         + '_lens_' +
                                         str(pkt.getLensPosition())
                                         + '_' + capture_time
                                         + '_' + str(pkt.getSequenceNum())
                                         )
                    capture_list.remove(c)
                    print()
                if c.startswith('raw_') or c.startswith('tof_amplitude_'):
                    if capture:
                        filename = capture_file_info + '_10bit.bw'
                        print('Saving:', filename)
                        frame.tofile(filename)
                    # Full range for display, use bits [15:6] of the 16-bit pixels
                    type = pkt.getType()
                    multiplier = 1
                    if type == dai.ImgFrame.Type.RAW10:
                        multiplier = (1 << (16-10))
                    if type == dai.ImgFrame.Type.RAW12:
                        multiplier = (1 << (16-4))
                    frame = frame * multiplier
                    # Debayer as color for preview/png
                    if cam_type_color[cam_skt]:
                        # See this for the ordering, at the end of page:
                        # https://docs.opencv.org/4.5.1/de/d25/imgproc_color_conversions.html
                        # TODO add bayer order to ImgFrame getType()
                        frame = cv2.cvtColor(frame, cv2.COLOR_BayerGB2BGR)
                else:
                    # Save YUV too, but only when RAW is also enabled (for tuning purposes)
                    if capture and args.enable_raw:
                        payload = pkt.getData()
                        filename = capture_file_info + '_P420.yuv'
                        print('Saving:', filename)
                        payload.tofile(filename)
                if capture:
                    filename = capture_file_info + '.png'
                    print('Saving:', filename)
                    cv2.imwrite(filename, frame)
                cv2.imshow(c, frame)
        print("\rFPS:",
              *["{:6.2f}|{:6.2f}".format(fps_host[c].get(),
                                         fps_capt[c].get()) for c in cam_list],
              end=' ', flush=True)
        if show:
            print()

        key = cv2.waitKey(1)
        if key == ord('q'):
            break
        elif key == ord('/'):
            show = not show
            # Print empty string as FPS status new-line separator
            print("" if show else "Printing camera settings: OFF")
        elif key == ord('c'):
            capture_list = streams.copy()
            capture_time = time.strftime('%Y%m%d_%H%M%S')
        elif key == ord('g') and tof:
            f_mod = dai.RawToFConfig.DepthParams.TypeFMod.MAX if tofConfig.depthParams.freqModUsed == dai.RawToFConfig.DepthParams.TypeFMod.MIN else dai.RawToFConfig.DepthParams.TypeFMod.MIN
            print("ToF toggling f_mod value to:", f_mod)
            tofConfig.depthParams.freqModUsed = f_mod
            tofCfgQueue.send(tofConfig)
        elif key == ord('h') and tof:
            tofConfig.depthParams.avgPhaseShuffle = not tofConfig.depthParams.avgPhaseShuffle
            print("ToF toggling avgPhaseShuffle value to:",
                  tofConfig.depthParams.avgPhaseShuffle)
            tofCfgQueue.send(tofConfig)
        elif key == ord('t'):
            print("Autofocus trigger (and disable continuous)")
            ctrl = dai.CameraControl()
            ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.AUTO)
            ctrl.setAutoFocusTrigger()
            controlQueue.send(ctrl)
        elif key == ord('f'):
            print("Autofocus enable, continuous")
            ctrl = dai.CameraControl()
            ctrl.setAutoFocusMode(
                dai.CameraControl.AutoFocusMode.CONTINUOUS_VIDEO)
            controlQueue.send(ctrl)
        elif key == ord('e'):
            print("Autoexposure enable")
            ctrl = dai.CameraControl()
            ctrl.setAutoExposureEnable()
            controlQueue.send(ctrl)
        elif key in [ord(','), ord('.')]:
            if key == ord(','):
                lensPos -= LENS_STEP
            if key == ord('.'):
                lensPos += LENS_STEP
            lensPos = clamp(lensPos, lensMin, lensMax)
            print("Setting manual focus, lens position: ", lensPos)
            ctrl = dai.CameraControl()
            ctrl.setManualFocus(lensPos)
            controlQueue.send(ctrl)
        elif key in [ord('i'), ord('o'), ord('k'), ord('l')]:
            if key == ord('i'):
                expTime -= EXP_STEP
            if key == ord('o'):
                expTime += EXP_STEP
            if key == ord('k'):
                sensIso -= ISO_STEP
            if key == ord('l'):
                sensIso += ISO_STEP
            expTime = clamp(expTime, expMin, expMax)
            sensIso = clamp(sensIso, sensMin, sensMax)
            print("Setting manual exposure, time: ", expTime, "iso: ", sensIso)
            ctrl = dai.CameraControl()
            ctrl.setManualExposure(expTime, sensIso)
            controlQueue.send(ctrl)
        elif key == ord('1'):
            awb_lock = not awb_lock
            print("Auto white balance lock:", awb_lock)
            ctrl = dai.CameraControl()
            ctrl.setAutoWhiteBalanceLock(awb_lock)
            controlQueue.send(ctrl)
        elif key == ord('2'):
            ae_lock = not ae_lock
            print("Auto exposure lock:", ae_lock)
            ctrl = dai.CameraControl()
            ctrl.setAutoExposureLock(ae_lock)
            controlQueue.send(ctrl)
        elif key == ord('a'):
            dotIntensity = dotIntensity - DOT_STEP
            if dotIntensity < 0:
                dotIntensity = 0
            print("Setting dot projector brightness: ", dotIntensity)
            device.setIrLaserDotProjectorBrightness(dotIntensity)
        elif key == ord('d'):
            dotIntensity = dotIntensity + DOT_STEP
            if dotIntensity > DOT_MAX:
                dotIntensity = DOT_MAX
            print("Setting dot projector brightness: ", dotIntensity)
            device.setIrLaserDotProjectorBrightness(dotIntensity)
        elif key == ord('w'):
            floodIntensity = floodIntensity + FLOOD_STEP
            if floodIntensity > FLOOD_MAX:
                floodIntensity = FLOOD_MAX
            print("Setting flood light brightness: ", floodIntensity)
            device.setIrFloodLightBrightness(floodIntensity)
        elif key == ord('s'):
            floodIntensity = floodIntensity - FLOOD_STEP
            if floodIntensity < 0:
                floodIntensity = 0
            print("Setting flood light brightness: ", floodIntensity)
            device.setIrFloodLightBrightness(floodIntensity)
        elif key >= 0 and chr(key) in '34567890[]p':
            if key == ord('3'):
                control = 'awb_mode'
            elif key == ord('4'):
                control = 'ae_comp'
            elif key == ord('5'):
                control = 'anti_banding_mode'
            elif key == ord('6'):
                control = 'effect_mode'
            elif key == ord('7'):
                control = 'brightness'
            elif key == ord('8'):
                control = 'contrast'
            elif key == ord('9'):
                control = 'saturation'
            elif key == ord('0'):
                control = 'sharpness'
            elif key == ord('['):
                control = 'luma_denoise'
            elif key == ord(']'):
                control = 'chroma_denoise'
            elif key == ord('p'):
                control = 'tof_amplitude_min'
            print("Selected control:", control)
        elif key in [ord('-'), ord('_'), ord('+'), ord('=')]:
            change = 0
            if key in [ord('-'), ord('_')]:
                change = -1
            if key in [ord('+'), ord('=')]:
                change = 1
            ctrl = dai.CameraControl()
            if control == 'none':
                print("Please select a control first using keys 3..9 0 [ ]")
            elif control == 'ae_comp':
                ae_comp = clamp(ae_comp + change, -9, 9)
                print("Auto exposure compensation:", ae_comp)
                ctrl.setAutoExposureCompensation(ae_comp)
            elif control == 'anti_banding_mode':
                abm = next(anti_banding_mode)
                print("Anti-banding mode:", abm)
                ctrl.setAntiBandingMode(abm)
            elif control == 'awb_mode':
                awb = next(awb_mode)
                print("Auto white balance mode:", awb)
                ctrl.setAutoWhiteBalanceMode(awb)
            elif control == 'effect_mode':
                eff = next(effect_mode)
                print("Effect mode:", eff)
                ctrl.setEffectMode(eff)
            elif control == 'brightness':
                brightness = clamp(brightness + change, -10, 10)
                print("Brightness:", brightness)
                ctrl.setBrightness(brightness)
            elif control == 'contrast':
                contrast = clamp(contrast + change, -10, 10)
                print("Contrast:", contrast)
                ctrl.setContrast(contrast)
            elif control == 'saturation':
                saturation = clamp(saturation + change, -10, 10)
                print("Saturation:", saturation)
                ctrl.setSaturation(saturation)
            elif control == 'sharpness':
                sharpness = clamp(sharpness + change, 0, 4)
                print("Sharpness:", sharpness)
                ctrl.setSharpness(sharpness)
            elif control == 'luma_denoise':
                luma_denoise = clamp(luma_denoise + change, 0, 4)
                print("Luma denoise:", luma_denoise)
                ctrl.setLumaDenoise(luma_denoise)
            elif control == 'chroma_denoise':
                chroma_denoise = clamp(chroma_denoise + change, 0, 4)
                print("Chroma denoise:", chroma_denoise)
                ctrl.setChromaDenoise(chroma_denoise)
            elif control == 'tof_amplitude_min' and tof:
                amp_min = clamp(
                    tofConfig.depthParams.minimumAmplitude + change, 0, 50)
                print("Setting min amplitude(confidence) to:", amp_min)
                tofConfig.depthParams.minimumAmplitude = amp_min
                tofCfgQueue.send(tofConfig)
            controlQueue.send(ctrl)


#include <exception>
#include <fstream>
#include <iostream>
#include <list>
#include <memory>
#include <string>
#include <vector>

// pybind11
#include "pybind11_common.hpp"

// depthai-core
#include "depthai/build/version.hpp"

// project
#include "depthai/depthai.hpp"
#include "pipeline/AssetManagerBindings.hpp"
#include "pipeline/PipelineBindings.hpp"
#include "pipeline/CommonBindings.hpp"
#include "pipeline/node/NodeBindings.hpp"
#include "XLinkBindings.hpp"
#include "DeviceBindings.hpp"
#include "CalibrationHandlerBindings.hpp"
#include "DeviceBootloaderBindings.hpp"
#include "DatatypeBindings.hpp"
#include "DataQueueBindings.hpp"
#include "openvino/OpenVINOBindings.hpp"
#include "log/LogBindings.hpp"
#include "VersionBindings.hpp"
#include "depthai_ros_py_bindings/bindings.hpp"

#ifdef DEPTHAI_PYTHON_EMBEDDED_MODULE
#include <pybind11/embed.h>
extern "C" void depthai_bindings_init() {} // to force inclusion
#endif

// Specify module
#ifdef DEPTHAI_PYTHON_EMBEDDED_MODULE
PYBIND11_EMBEDDED_MODULE(depthai, m)
#else
PYBIND11_MODULE(depthai, m)
#endif
{

    // Depthai python version consists of: (depthai-core).(bindings revision)[+bindings hash]
    m.attr("__version__") = DEPTHAI_PYTHON_VERSION;
    m.attr("__commit__") = DEPTHAI_PYTHON_COMMIT_HASH;
    m.attr("__commit_datetime__") = DEPTHAI_PYTHON_COMMIT_DATETIME;
    m.attr("__build_datetime__") = DEPTHAI_PYTHON_BUILD_DATETIME;
    m.attr("__device_version__") = dai::build::DEVICE_VERSION;
    m.attr("__bootloader_version__") = dai::build::BOOTLOADER_VERSION;
    m.attr("__device_rvc3_version__") = dai::build::DEVICE_RVC3_VERSION;

    // Add bindings
    std::deque<StackFunction> callstack;
    DatatypeBindings::addToCallstack(callstack);
    callstack.push_front(&LogBindings::bind);
    callstack.push_front(&VersionBindings::bind);
    callstack.push_front(&DataQueueBindings::bind);
    callstack.push_front(&OpenVINOBindings::bind);
    NodeBindings::addToCallstack(callstack);
    callstack.push_front(&AssetManagerBindings::bind);
    callstack.push_front(&PipelineBindings::bind);
    callstack.push_front(&XLinkBindings::bind);
    callstack.push_front(&DeviceBindings::bind);
    callstack.push_front(&DeviceBootloaderBindings::bind);
    callstack.push_front(&CalibrationHandlerBindings::bind);
    callstack.push_front(&dai::ros::RosBindings::bind);
    // end of the callstack
    callstack.push_front([](py::module &, void *) {});

    Callstack callstackAdapter(callstack);

    // Initial call
    CommonBindings::bind(m, &callstackAdapter);

    // Install signal handler option
    bool installSignalHandler = true;
    constexpr static const char* signalHandlerKey = "DEPTHAI_INSTALL_SIGNAL_HANDLER";
    try {
        auto sysModule = py::module_::import("sys");
        if(py::hasattr(sysModule, signalHandlerKey)){
            installSignalHandler = installSignalHandler && sysModule.attr(signalHandlerKey).cast<bool>();
        }
    } catch (...) {
        // ignore
    }
    try {
        auto builtinsModule = py::module_::import("builtins");
        if(py::hasattr(builtinsModule, signalHandlerKey)){
            installSignalHandler = installSignalHandler && builtinsModule.attr(signalHandlerKey).cast<bool>();
        }
    } catch (...){
        // ignore
    }

    // Call dai::initialize on 'import depthai' to initialize asap with additional information to print
    try {
        dai::initialize(std::string("Python bindings - version: ") + DEPTHAI_PYTHON_VERSION + " from " + DEPTHAI_PYTHON_COMMIT_DATETIME + " build: " + DEPTHAI_PYTHON_BUILD_DATETIME, installSignalHandler);
    } catch (const std::exception&) {
        // ignore, will be initialized later on if possible
    }

}

import onnxruntime as ort  # Added onnxruntime
# from openvino import Core  # Removed OpenVINO Core
import cv2
import numpy as np

from ok import Logger, Box, sort_boxes, og  # Assuming these are available
from src.yolo_common import letterbox

logger = Logger.get_logger(__name__)


class OnnxYolo8Detect:  # Renamed class

    def __init__(self, weights='echo.onnx', model_h=640, model_w=640, iou_thres=0.45):
        """
        yolov ONNX Runtime inference
        dic_labels: {0: 'person', 1: 'bicycle'}
        """
        self.dic_labels = {0: 'echo'}
        self.weights = weights
        # Store model_h and model_w for preprocessing.
        # These will be the target dimensions for the letterbox function.
        self.preprocess_target_h = model_h
        self.preprocess_target_w = model_w
        self.iou_threshold = iou_thres

        # --- ONNX Runtime Initialization ---
        options = ort.SessionOptions()
        # options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL # Optional

        available_providers = ort.get_available_providers()
        logger.info(f"Available ONNX Runtime providers: {available_providers}")

        # Prioritize DirectML, then CUDA, then CPU
        providers = []
        if og.use_dml and 'DmlExecutionProvider' in available_providers:
            logger.info("Attempting to use DirectML Execution Provider.")
            providers.append(('DmlExecutionProvider', {'device_id': 0}))
        elif 'CUDAExecutionProvider' in available_providers:
            logger.info("Attempting to use CUDA Execution Provider.")
            providers.append(('CUDAExecutionProvider', {'device_id': 0}))

        providers.append('CPUExecutionProvider')  # Always include CPU as a fallback

        try:
            logger.info(f"Initializing ONNX Runtime session with providers: {providers} for model: {self.weights}")
            self.session = ort.InferenceSession(self.weights, sess_options=options, providers=providers)

            # Get input/output names
            self.input_name = self.session.get_inputs()[0].name
            self.output_name = self.session.get_outputs()[0].name

            # Get model input shape
            model_input_shape = self.session.get_inputs()[0].shape  # e.g., [1, 3, 640, 640] for NCHW
            self.model_actual_input_h = model_input_shape[2]
            self.model_actual_input_w = model_input_shape[3]

            if self.preprocess_target_h != self.model_actual_input_h or \
                    self.preprocess_target_w != self.model_actual_input_w:
                logger.warning(
                    f"User-specified preprocessing HxW ({self.preprocess_target_h}x{self.preprocess_target_w}) "
                    f"differs from ONNX model's expected input HxW ({self.model_actual_input_h}x{self.model_actual_input_w}). "
                    f"Using user-specified dimensions ({self.preprocess_target_h}x{self.preprocess_target_w}) for preprocessing. "
                    "Ensure this is intended and the model supports dynamic input sizes or this specific size."
                )

            logger.info(f"ONNX Runtime model loaded successfully using {self.session.get_providers()}.")
            logger.info(f"Model Input: '{self.input_name}' with shape {model_input_shape}")
            logger.info(f"Model Output: '{self.output_name}' with shape {self.session.get_outputs()[0].shape}")

        except Exception as e:
            logger.error(f"Error initializing ONNX Runtime session: {e}")
            raise RuntimeError("Could not initialize ONNX Runtime model") from e
        # --- End ONNX Runtime Initialization ---


    def _preprocess(self, img):
        """图像预处理（保持宽高比的缩放填充）"""
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # Use preprocess_target_h and preprocess_target_w for letterboxing
        img_letterboxed, pad = letterbox(img_rgb, (self.preprocess_target_h, self.preprocess_target_w))

        image_data = np.array(img_letterboxed) / 255.0
        image_data = np.transpose(image_data, (2, 0, 1))  # Channel first HWC to CHW
        image_data = np.expand_dims(image_data, axis=0).astype(np.float32)

        return image_data, pad

    def _postprocess(self, outputs_from_model, padding, orig_shape, confidence_threshold, label):
        """
        Perform post-processing on the model's output.
        """
        # outputs_from_model is a list from session.run(), take the first element.
        processed_outputs = np.transpose(np.squeeze(outputs_from_model[0]))
        rows = processed_outputs.shape[0]

        boxes = []
        scores = []
        class_ids = []

        # The 'gain' calculation below replicates the logic from the original OpenVINO-based code.
        # In the original code, self.input_width stored model height, and self.input_height stored model width.
        # The gain was calculated as: min(variable_storing_width / original_image_height, variable_storing_height / original_image_width).
        # To replicate this with current variable names (where preprocess_target_h is height, preprocess_target_w is width):
        # gain = min(self.preprocess_target_w / orig_shape[0], self.preprocess_target_h / orig_shape[1])
        # This calculation for 'gain' might be incorrect if orig_shape is (height, width) as it would mix width/height ratios.
        # A more standard gain calculation would be:
        # gain = min(self.preprocess_target_h / orig_shape[0], self.preprocess_target_w / orig_shape[1])
        # However, to adhere to "do not fix bugs", the original logic is replicated.
        gain = min(self.preprocess_target_w / orig_shape[0], self.preprocess_target_h / orig_shape[1])

        # Adjust detections for padding
        # padding is (pad_top, pad_left)
        # processed_outputs[:, 0] are x-centers, processed_outputs[:, 1] are y-centers
        processed_outputs[:, 0] -= padding[1]  # Adjust x-coordinates by left padding
        processed_outputs[:, 1] -= padding[0]  # Adjust y-coordinates by top padding

        for i in range(rows):
            classes_scores = processed_outputs[i][4:]
            max_score = np.amax(classes_scores)
            class_id = np.argmax(classes_scores)

            if max_score >= confidence_threshold and (label == -1 or label == class_id):
                x, y, w, h = processed_outputs[i][0], processed_outputs[i][1], processed_outputs[i][2], \
                    processed_outputs[i][3]

                left = int((x - w / 2) / gain)
                top = int((y - h / 2) / gain)
                width = int(w / gain)
                height = int(h / gain)

                class_ids.append(class_id)
                scores.append(max_score)
                boxes.append([left, top, width, height])

        indices = cv2.dnn.NMSBoxes(boxes, scores, confidence_threshold, self.iou_threshold)

        results = []
        # Check if indices is not an empty tuple, which can happen if NMSBoxes returns nothing.
        if len(indices) > 0:
            # In OpenCV 4.x NMSBoxes returns a 2D array if more than one box, 1D if one.
            # Flatten in case it's [[0], [1]] etc.
            if isinstance(indices, tuple):  # Should not happen with current cv2 versions but defensive
                indices_flat = np.array(indices).flatten()
            else:  # numpy array
                indices_flat = indices.flatten()

            for i in indices_flat:
                box = boxes[i]
                box_obj = Box(box[0], box[1], box[2], box[3])
                box_obj.name = self.dic_labels.get(int(class_ids[i]), 'unknown')
                box_obj.confidence = scores[i]
                results.append(box_obj)
        return results

    def detect(self, image, threshold=0.5, label=-1):
        '''
        预测
        '''
        try:
            h, w = image.shape[:2]
            img_data, pad = self._preprocess(image)

            # --- ONNX Runtime Inference ---
            # Input is a dictionary {input_name: data}
            # Output is a list of numpy arrays
            outputs = self.session.run([self.output_name], {self.input_name: img_data})
            # --- End ONNX Runtime Inference ---

            boxes = self._postprocess(outputs, pad, (h, w), threshold, label)

            return sort_boxes(boxes)  # Assuming sort_boxes is available
        except Exception as e:
            logger.error(f'ONNX Runtime yolo detect error: {e}')
            return []

import cv2
import numpy as np
import matplotlib.pyplot as plt
import math
from ultralytics import YOLO

"""
실제 계산
1. crop한 이미지 상의 좌표를 구함
2. 원본 사진으로 좌표 이동
3. 이미지 상의 상자 크기 구하기
4. 초점거리, 원본사진의 중점, 2D좌표, 이미자 상의 박스 크기를 이용해 임의로 정한 3D좌표를 이용해 카메라 외부 파라미터(rvec, tvec)을 구함
5. Rodrigues(rvec)으로 진짜 카메라 정보를 얻은 후, 실제 월드 좌표계의 박스 한 점과, 카메라의 좌표를 구한후 둘 사이 거리 구함
6. 카메라 초점거리 : 실제 카메라와 물체간 거리 = 이미지 상 박스 크기 : 실제 박스크기 비례식을 이용해 박스 크기 산출
모든 계산 식과 내용은 https://darkpgmr.tistory.com/153 참고
"""


def calculate_box_real_length(edges, original, fx, fy):
    """
    윤곽선만 검출한 이미지와 초점거리(fx, fy)로 박스의 실제 가로 세로 높이를 계산한다.
    """
    points = find_points_from_edges_image(edges)
    top, bottom, left_top, left_bottom, right_top, right_bottom = classify_points(points)
    away_x, away_y = min(left_top[0], left_bottom[0]), top[1]
    top, bottom, left_top, left_bottom, right_top, right_bottom = adjust_points(top, bottom, left_top, left_bottom, right_top, right_bottom, away_x, away_y, original, edges)
    width, height, tall = calc_pixel_w_h(top, bottom, left_top, left_bottom, right_top, right_bottom)
    cx, cy = original.shape[1] / 2, original.shape[0] / 2
    retval, rvec, tvec = calculate_parameters(fx, fy, cx, cy, top, bottom, left_top, left_bottom, right_top, right_bottom, width, height, tall)
    distance = calculate_distance(rvec, tvec, bottom, fx, fy, cx, cy)

    return calculate_real_length(width, height, tall, distance, fx)


def classify_points(points):
    # y 좌표가 가장 낮은 점을 찾아 맨 위 점으로 설정
    top = min(points, key=lambda p: p[1])
    points.remove(top)

    # y 좌표가 가장 높은 점을 찾아 맨 밑 점으로 설정
    bottom = max(points, key=lambda p: p[1])
    points.remove(bottom)

    # x 좌표가 가장 작은 두 점을 찾아 왼쪽 점으로 설정
    left_points = sorted(points, key=lambda p: p[0])[:2]
    for p in left_points:
        points.remove(p)

    # 남은 두 점은 오른쪽 점으로 설정
    right_points = points

    # 각 왼쪽과 오른쪽의 점들을 높이에 따라 위쪽과 아래쪽으로 분류
    left_top = min(left_points, key=lambda p: p[1])
    left_bottom = max(left_points, key=lambda p: p[1])
    right_top = min(right_points, key=lambda p: p[1])
    right_bottom = max(right_points, key=lambda p: p[1])

    return top, bottom, left_top, left_bottom, right_top, right_bottom


def calc_pixel_w_h(top, bottom, left_top, left_bottom, right_top, right_bottom):
    """
    이미지 좌표계 상의 가로, 세로, 높이를 추정하는 함수
    """

    width = (math.sqrt((top[0] - left_top[0])**2 + (top[1] - left_top[1])**2) +
             math.sqrt((bottom[0] - right_bottom[0])**2 + (bottom[1] - right_bottom[1])**2)) / 2
    height = (math.sqrt((top[0] - right_top[0])**2 + (top[1] - right_top[1])**2) +
             math.sqrt((bottom[0] - left_bottom[0])**2 + (bottom[1] - left_bottom[1])**2)) / 2
    tall = (math.sqrt((left_top[0] - left_bottom[0])**2 + (left_top[1] - left_bottom[1])**2) + 
            math.sqrt((right_top[0] - right_bottom[0])**2 + (right_top[1] - right_bottom[1])**2)) / 2

    return width, height, tall


def find_points_from_edges_image(edges):
    """
    윤곽선만 검출한 이미지에서 최대 점 6개를 가진 도형들의 꼭짓점들을 검출
    """
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hexagon_contours = []

    # 육각형 윤곽선 필터링
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) <= 6:
            hexagon_contours.append(approx)
    
    # 근사화된 윤곽선에서 각 꼭지점의 좌표 추출 및 표시
    points = []
    for hexagon in hexagon_contours:
        for point in hexagon:
            x, y = point[0]
            
            points.append((x, y))

    return points


def calculate_parameters(fx, fy, cx, cy, top, bottom, left_top, left_bottom, right_top, right_bottom, width, height, tall):
    """
    외부 파라미터 추정
    """
    #2D 이미지 좌표
    image_points = np.array([[bottom[0], bottom[1]],
                            [left_bottom[0], left_bottom[1]],
                            [right_bottom[0], right_bottom[1]],
                            [left_top[0], left_top[1]], 
                            [right_top[0], right_top[1]],
                            [top[0], top[1]]], 
                            dtype=np.float32)
    #3D 좌표계에 생성한 박스 좌표
    object_points = np.array([[0, 0, 0],
                            [width, 0, 0],
                            [0, height, 0],
                            [width, 0, tall],
                            [0, height, tall],
                            [width, height, tall]],
                            dtype=np.float32)
    cameraMatrix = np.array([[fx, 0, cx],
                            [0, fy, cy],
                            [0, 0, 1]],
                            dtype=np.float32)

    return cv2.solvePnP(object_points, image_points, cameraMatrix, np.zeros(5), flags=cv2.SOLVEPNP_ITERATIVE)


def calculate_distance(rvec, tvec, bottom, fx, fy, cx, cy):
    """
    물체와 카메라 사이의 거리 계산
    """
    #3D좌표계의 원점을 실제 월드 좌표계의 점으로 변환
    #구한 R벡터를 원래 회전정보 행렬로 변환
    Ro, _ = cv2.Rodrigues(rvec)

    #3D좌표계의 원점을 구하므로, 카메라 좌표계상 박스 맨 밑점(bottom = 0) 이므로 Pc = tvec
    Pc = tvec

    #픽셀좌표의 정규좌표화
    u = (bottom[0] - cx) / fx
    v = (bottom[1] - cy) / fy

    #정규좌표상의 bottom 좌표
    p_c = np.array([[u], [v], [1]], dtype=np.float32)
    #카메라 원점의 카메라좌표
    C_c = np.array([[0], [0], [0]], dtype=np.float32)
    #월드좌표상의 bottom 좌표
    p_w = Ro.transpose()@(p_c - tvec)
    #월드좌표상의 카메라 좌표
    C_w = Ro.transpose()@(C_c - tvec)

    #지면과 맞닿는 점을 P라 할때, P = C_w + k * (p_w - C_w) 성립,
    #월드좌표계상 지면은 Z = 0이므로 k를 구할 수 있음
    k = -C_w[2]/(p_w[2] - C_w[2])

    #지면좌표상의 bottom 좌표
    ground_x = C_w[0] + k*(p_w[0] - C_w[0])
    ground_y = C_w[1] + k*(p_w[1] - C_w[1])

    #실제 카메라와의 거리
    return math.sqrt((C_w[0] - ground_x)**2 + (C_w[1] - ground_y)**2 + C_w[2]**2)


def calculate_real_length(width, height, tall, distance, fx):
    """
    카메라와의 거리를 바탕으로 실제 거리 계산
    """
    #카메라와 거리 : 초점거리 = 실제 박스크기 : 이미지상 박스크기
    real_width = round(width * distance / fx, 2)
    real_height = round(height * distance / fx, 2)
    real_tall = round(tall * distance / fx, 2)

    return real_width, real_height, real_tall

def adjust_points(top, bottom, left_top, left_bottom, right_top, right_bottom, away_x, away_y, original, edges):
    #NOTE: 일단 테스트시엔 보기 편하게 이렇게 넣었습니다.
    model = YOLO('yolo-v8/detect_model.pt')
    source = 'yolo-v8/images/test2.jpg'
    results = model(source)
    boxes = results[0].boxes
    box = boxes[0] 

    points = [top, bottom, left_top, left_bottom, right_top, right_bottom]
    new_points = []
    for point in points:
        new_points.append((point[0] + box.xyxy[0][0], point[1] + box.xyxy[0][1]))

    return new_points[0], new_points[1], new_points[2], new_points[3], new_points[4], new_points[5]

def main():
    input_path = 'findDot/crops/test2.png'
    original_path = 'yolo-v8/images/test2.jpg'
    original = cv2.imread(original_path)
    #윤곽선만 검출한 이미지 가져오기
    edges = cv2.imread(input_path)
    edges = cv2.cvtColor(edges, cv2.COLOR_BGR2GRAY)

    points = find_points_from_edges_image(edges)


    # 찾은 점 시각화
    plt.imshow(edges)
    for x, y in points:
        plt.scatter(x, y, color='red', s=10)
    plt.show()


    top, bottom, left_top, left_bottom, right_top, right_bottom = classify_points(points)

    #좌표 원본이미지에 맞게 보정
    away_x, away_y = min(left_top[0], left_bottom[0]), top[1]
    print(away_x, away_y)

    #좌표 조정
    top, bottom, left_top, left_bottom, right_top, right_bottom = adjust_points(top, bottom, left_top, left_bottom, right_top, right_bottom, away_x, away_y, original, edges)
    new_points = [top, bottom, left_top, left_bottom, right_top, right_bottom]
    plt.imshow(original)
    for x, y in new_points:
        plt.scatter(x, y, color='red', s=10)
    plt.show()
    #이미지 꼭지점 좌표를 토대로 구한 가로, 세로, 높이
    width, height, tall = calc_pixel_w_h(top, bottom, left_top, left_bottom, right_top, right_bottom)
    print(width, height, tall)

    #TODO: 카메라의 초점거리와 셀 크기를 알아오는 작업 필요
    fx, fy, cx, cy = 4777.7, 4777.7, original.shape[1] / 2, original.shape[0] / 2

    #외부 파라미터 추정
    retval, rvec, tvec = calculate_parameters(fx, fy, cx, cy, top, bottom, left_top, left_bottom, right_top, right_bottom, width, height, tall)

    print(rvec, tvec)
    # 시각화용 코드
    # 3D 좌표계 상에서 카메라의 위치와 방향 계산
    rotation_matrix, _ = cv2.Rodrigues(rvec)
    camera_position = -np.dot(rotation_matrix.T, tvec)

    # 3D 그래프 생성
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')

    # 카메라의 위치와 방향 그리기
    ax.quiver(camera_position[0], camera_position[1], camera_position[2], rvec[0], rvec[1], rvec[2])

    #3D 좌표계에 생성한 박스 좌표
    object_points = np.array([[0, 0, 0],
                            [width, 0, 0],
                            [0, height, 0],
                            [width, 0, tall],
                            [0, height, tall],
                            [width, height, tall]],
                            dtype=np.float32)

    #물체 위치 그리기
    ax.scatter3D(object_points[:, 0], object_points[:, 1], object_points[:, 2])

    # 그래프 표시
    plt.show()

    distance = calculate_distance(rvec, tvec, bottom, fx, fy, cx, cy)
    print(distance)

    w, h, t = calculate_real_length(width, height, tall, distance, fx)
    print(w, h, t)


def main2():
    input_path = 'findDot/crops/crop11.png'

    #윤곽선만 검출한 이미지 가져오기
    edges = cv2.imread(input_path)
    edges = cv2.cvtColor(edges, cv2.COLOR_BGR2GRAY)
    #TODO: 카메라의 초점거리와 셀 크기(이미지센서 크기)를 알아오는 작업 필요
    fx, fy = 944.4, 944.4
    w, h, t = calculate_box_real_length(edges, fx, fy)
    print(w, h, t)


if __name__ == "__main__":
    main()
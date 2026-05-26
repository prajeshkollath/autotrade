def bubble_sort(nums):
    n = len(nums)
    for i in range(n):
        for j in range(0, n - i - 1):
            if nums[j] > nums[j + 1]:
                nums[j], nums[j + 1] = nums[j + 1], nums[j]
    return nums


if __name__ == "__main__":
    result = bubble_sort([5, 2, 8, 1, 9, 3])
    print(result)
